import base64
import hashlib
import json
from datetime import datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.credential_lease_service import (
    CredentialLeaseError,
    CredentialLeaseService,
    _lease_token_hash,
)
from sacm.core.evidence_service import EvidenceService
from sacm.core.execution_plane_service import _token_hash
from sacm.core.run_service import RunService
from sacm.core.secret_broker import (
    EnterpriseSecretBroker,
    ProviderCredential,
    SecretProvider,
    SecretProviderError,
)
from sacm.core.tenancy_service import AuthorizationError, TenancyService
from sacm.infrastructure.db.models import (
    Base,
    CredentialLease,
    ExecutionJob,
    ExecutorRegistration,
    RunStep,
    RuntimeEvent,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.execution_plane import SecretProviderConfigCreate
from sacm.schemas.run import RunCreate

RAW_PROVIDER_VALUE = "provider-value-that-must-never-be-persisted"
RAW_PROVIDER_LEASE_ID = "vault-lease-id-that-must-never-be-persisted"


class StaticProvider(SecretProvider):
    name = "environment"

    def validate_config(self, config):
        assert config == {}

    def health(self, config):
        return {"healthy": True}

    def fetch(self, request, config, *, ttl_seconds):
        return ProviderCredential(
            value=RAW_PROVIDER_VALUE.encode(),
            content_type="text/plain",
            expires_at=datetime.utcnow() + timedelta(seconds=ttl_seconds),
            provider_lease_id=RAW_PROVIDER_LEASE_ID,
        )


def _setup_job(db):
    tenancy = TenancyService(db)
    organization = tenancy.create_organization("secrets", "Secrets", "owner")
    project = tenancy.create_project(
        organization.id, "runtime", "Runtime", "owner"
    )
    run = RunService(db).create(
        RunCreate(
            title="Credential exchange",
            description="Test wrapped credential delivery.",
            project_id=project.id,
        )
    )
    step = RunStep(
        run_id=run.id,
        sequence=1,
        name="remote",
        idempotency_key="remote",
    )
    encryption_private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    )
    public_encryption_key = encryption_private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    public_der = encryption_private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    executor = ExecutorRegistration(
        project_id=project.id,
        scope_key=f"project:{project.id}",
        executor_identity="executor",
        display_name="Executor",
        capabilities=["agent-task/v1"],
        labels={},
        runtime_kind="docker",
        sandbox_runtime="runsc",
        sandbox_policy={},
        public_signing_key="unused-public-signing-key",
        signing_key_fingerprint="a" * 64,
        public_encryption_key=public_encryption_key,
        encryption_key_fingerprint=hashlib.sha256(public_der).hexdigest(),
        auth_token_hash="executor-auth-hash",
        status="ACTIVE",
        last_heartbeat_at=datetime.utcnow(),
        version="1",
        network_boundary={},
    )
    db.add_all([step, executor])
    db.flush()
    lease_token = "j" * 48
    job = ExecutionJob(
        organization_id=organization.id,
        project_id=project.id,
        scope_key=f"project:{project.id}",
        run_id=run.id,
        run_step_id=step.id,
        task_id=run.task_id,
        state="RUNNING",
        idempotency_key="enterprise-secret-job",
        required_capabilities=["agent-task/v1"],
        required_labels={},
        secret_requirements=[
            {
                "name": "deployment-token",
                "purpose": "Deploy the owned job.",
                "environment_variable": "ENTERPRISE_TEST_SECRET",
                "required": True,
                "step_keys": [],
            }
        ],
        payload_contract={},
        payload_hash="a" * 64,
        payload_signature="signature",
        payload_signature_metadata={},
        lease_owner_id=executor.id,
        lease_token_hash=_lease_token_hash(lease_token),
        lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db.add(job)
    db.commit()
    return organization, project, run, executor, job, lease_token, encryption_private_key


def _decrypt(private_key, wrapped: str) -> str:
    envelope = json.loads(base64.b64decode(wrapped))
    key = private_key.decrypt(
        base64.b64decode(envelope["encrypted_key"]),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    value = AESGCM(key).decrypt(
        base64.b64decode(envelope["nonce"]),
        base64.b64decode(envelope["ciphertext"]),
        None,
    )
    return value.decode()


def _database_text(db) -> str:
    connection = db.connection()
    content: list[str] = []
    for table_name in inspect(connection).get_table_names():
        rows = connection.execute(text(f'SELECT * FROM "{table_name}"')).mappings()
        content.extend(json.dumps(dict(row), default=str) for row in rows)
    return "\n".join(content)


def test_one_time_exchange_wraps_value_and_persists_only_metadata(
    db, monkeypatch
):
    monkeypatch.setenv("ENTERPRISE_TEST_SECRET", RAW_PROVIDER_VALUE)
    organization, _, _, executor, job, job_token, private_key = _setup_job(db)
    broker = EnterpriseSecretBroker({"environment": StaticProvider()})
    service = CredentialLeaseService(db, broker)

    lease = service.issue(
        executor,
        job_id=job.id,
        lease_token=job_token,
        requirement_name="deployment-token",
        ttl_seconds=120,
    )
    wrapped = service.exchange(executor, lease.id, job_token)

    assert _decrypt(private_key, wrapped.ciphertext) == RAW_PROVIDER_VALUE
    assert RAW_PROVIDER_VALUE not in wrapped.ciphertext
    assert db.get(CredentialLease, lease.id).use_count == 1
    assert db.get(CredentialLease, lease.id).provider_lease_id_hash
    persisted = _database_text(db)
    assert RAW_PROVIDER_VALUE not in persisted
    assert RAW_PROVIDER_LEASE_ID not in persisted
    assert "BEGIN PRIVATE KEY" not in persisted
    assert RAW_PROVIDER_VALUE not in json.dumps(
        {
            key: value
            for key, value in lease.__dict__.items()
            if not key.startswith("_")
        },
        default=str,
    )
    assert all(
        RAW_PROVIDER_VALUE not in json.dumps(event.payload)
        for event in db.query(RuntimeEvent).all()
    )
    assert EvidenceService._sanitize({"message": RAW_PROVIDER_VALUE}) == {
        "message": "[REDACTED]"
    }
    events = (
        db.query(RuntimeEvent)
        .filter(RuntimeEvent.run_id == job.run_id)
        .order_by(RuntimeEvent.sequence)
        .all()
    )
    assert events[-1].previous_event_hash == events[-2].event_hash

    listed = service.list_leases(organization.id, "owner")
    assert listed[0].id == lease.id
    assert not hasattr(listed[0], "credential_value")


def test_exchange_fails_closed_for_ownership_declaration_and_reuse(db):
    _, project, _, executor, job, job_token, _ = _setup_job(db)
    broker = EnterpriseSecretBroker({"environment": StaticProvider()})
    service = CredentialLeaseService(db, broker)

    with pytest.raises(AuthorizationError, match="not declared"):
        service.issue(
            executor,
            job_id=job.id,
            lease_token=job_token,
            requirement_name="undeclared",
            ttl_seconds=60,
        )
    other = ExecutorRegistration(
        project_id=project.id,
        scope_key=f"project:{project.id}",
        executor_identity="other",
        display_name="Other",
        capabilities=[],
        labels={},
        runtime_kind="docker",
        sandbox_runtime="runsc",
        sandbox_policy={},
        public_signing_key="unused-public-signing-key",
        signing_key_fingerprint="b" * 64,
        auth_token_hash="other-auth-hash",
        status="ACTIVE",
        last_heartbeat_at=datetime.utcnow(),
        version="1",
        network_boundary={},
    )
    db.add(other)
    db.commit()
    with pytest.raises(AuthorizationError, match="another executor"):
        service.issue(
            other,
            job_id=job.id,
            lease_token=job_token,
            requirement_name="deployment-token",
            ttl_seconds=60,
        )

    lease = service.issue(
        executor,
        job_id=job.id,
        lease_token=job_token,
        requirement_name="deployment-token",
        ttl_seconds=60,
    )
    service.exchange(executor, lease.id, job_token)
    with pytest.raises(CredentialLeaseError, match="one-time"):
        service.exchange(executor, lease.id, job_token)
    with pytest.raises(CredentialLeaseError, match="cannot be renewed"):
        service.renew(executor, lease.id, job_token, 60)


def test_provider_configuration_rejects_embedded_credentials(db):
    organization = TenancyService(db).create_organization(
        "provider-config", "Provider Config", "owner"
    )
    service = CredentialLeaseService(db)

    with pytest.raises(SecretProviderError, match="may only reference"):
        service.create_provider_config(
            organization.id,
            "owner",
            SecretProviderConfigCreate(
                name="vault",
                provider="vault",
                config_metadata={
                    "address": "https://vault.example",
                    "token": "raw-token",
                },
            ),
        )

    configured = service.create_provider_config(
        organization.id,
        "owner",
        SecretProviderConfigCreate(
            name="vault",
            provider="vault",
            approved_for_production=True,
            config_metadata={
                "address": "https://vault.example",
                "token_env": "VAULT_TOKEN",
            },
        ),
    )
    assert configured.config_metadata["token_env"] == "VAULT_TOKEN"


def test_executor_exchange_api_returns_ciphertext_only(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_TEST_SECRET", RAW_PROVIDER_VALUE)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    organization, _, _, executor, job, job_token, _ = _setup_job(db)
    executor_token = "e" * 48
    executor.auth_token_hash = _token_hash("executor-auth", executor_token)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            issued = client.post(
                "/v1/executor/credential-leases",
                headers={"Authorization": f"Bearer {executor_token}"},
                json={
                    "job_id": job.id,
                    "lease_token": job_token,
                    "requirement_name": "deployment-token",
                    "ttl_seconds": 60,
                },
            )
            assert issued.status_code == 201
            assert RAW_PROVIDER_VALUE not in issued.text
            assert job_token not in issued.text
            lease = issued.json()

            exchange = client.post(
                f"/v1/executor/credential-leases/"
                f"{lease['opaque_handle']}/exchange",
                headers={"Authorization": f"Bearer {executor_token}"},
                json={"lease_token": job_token},
            )
            assert exchange.status_code == 200
            assert RAW_PROVIDER_VALUE not in exchange.text
            assert set(exchange.json()) == {
                "ciphertext",
                "wrapping_algorithm",
                "encryption_key_fingerprint",
                "content_type",
                "expires_at",
            }
            repeated = client.post(
                f"/v1/executor/credential-leases/{lease['id']}/exchange",
                headers={"Authorization": f"Bearer {executor_token}"},
                json={"lease_token": job_token},
            )
            assert repeated.status_code == 409

            listed = client.get(
                f"/v1/organizations/{organization.id}/credential-leases",
                headers={"X-SACM-Actor": "owner"},
            )
            assert listed.status_code == 200
            assert RAW_PROVIDER_VALUE not in listed.text
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
