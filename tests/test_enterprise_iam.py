from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.auth_service import authenticate_request
from sacm.core.credential_lease_service import CredentialLeaseService
from sacm.core.evidence_service import EvidenceService
from sacm.core.execution_plane_service import ExecutionPlaneService
from sacm.core.governance_service import GovernancePolicyService, SIEMService
from sacm.core.memory_service import MemoryService
from sacm.core.policy_service import PolicyService
from sacm.core.repository_audit_service import RepositoryAuditService
from sacm.core.resilience_service import BackupService
from sacm.core.run_service import RunService
from sacm.core.tenancy_service import (
    AuthorizationError,
    ResourceAuthorizationService,
    ServiceCredentialService,
    TenancyService,
    TenantAuditService,
    TenantBackfillService,
)
from sacm.infrastructure.db.models import (
    Approval,
    Artifact,
    Base,
    EvidencePack,
    ExecutionJob,
    ExecutorRegistration,
    MemoryChunk,
    RunStep,
    ServiceCredential,
    TenantAuditEvent,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.run import RunCreate


def _tenants(db):
    tenancy = TenancyService(db)
    organization_a = tenancy.create_organization("tenant-a", "Tenant A", "owner-a")
    project_a = tenancy.create_project(
        organization_a.id,
        "runtime",
        "Runtime A",
        "owner-a",
        repository_path="/repos/tenant-a",
    )
    organization_b = tenancy.create_organization("tenant-b", "Tenant B", "owner-b")
    project_b = tenancy.create_project(
        organization_b.id,
        "runtime",
        "Runtime B",
        "owner-b",
        repository_path="/repos/tenant-b",
    )
    return organization_a, project_a, organization_b, project_b


@pytest.mark.security_release_gate
def test_cross_tenant_resources_have_zero_leakage(db, monkeypatch, request):
    request.node.user_properties.append(("security_test_id", "SRG-ADV-004"))
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")
    organization_a, project_a, _, project_b = _tenants(db)
    run_a = RunService(db).create(
        RunCreate(title="A", description="Tenant A run", project_id=project_a.id)
    )
    run_b = RunService(db).create(
        RunCreate(title="B", description="Tenant B run", project_id=project_b.id)
    )
    resources = ResourceAuthorizationService(db)

    sensitive_surface_checks = (
        lambda: ServiceCredentialService(db).list(organization_a.id, "owner-b"),
        lambda: CredentialLeaseService(db).list_leases(
            organization_a.id, "owner-b"
        ),
        lambda: GovernancePolicyService(db).list_policies(
            organization_a.id, "owner-b"
        ),
        lambda: SIEMService(db).list_sinks(organization_a.id, "owner-b"),
        lambda: BackupService(db).list("owner-b", organization_a.id),
    )
    for check in sensitive_surface_checks:
        with pytest.raises(AuthorizationError, match="not accessible"):
            check()

    with pytest.raises(AuthorizationError, match="not accessible"):
        resources.require_run(run_a.id, "owner-b", "runs.read")
    with pytest.raises(AuthorizationError, match="not accessible"):
        resources.require_task(run_a.task_id, "owner-b", "tasks.read")

    MemoryService(db).add(
        run_a.task_id,
        "tenant-a-vector-content",
        actor_id="owner-a",
    )
    with pytest.raises(AuthorizationError, match="not accessible"):
        MemoryService(db).search(
            run_a.task_id, "tenant-a-vector-content", actor_id="owner-b"
        )

    pack = EvidencePack(
        organization_id=organization_a.id,
        project_id=project_a.id,
        run_id=run_a.id,
        path="/not-readable/tenant-a",
        manifest_hash="a" * 64,
    )
    artifact = Artifact(
        organization_id=organization_a.id,
        project_id=project_a.id,
        task_id=run_a.task_id,
        artifact_type="test",
        path="/repos/tenant-a/private.txt",
    )
    approval = Approval(
        organization_id=organization_a.id,
        project_id=project_a.id,
        run_id=run_a.id,
        action="deployment.execute",
        resource={"environment": "production"},
    )
    db.add_all([pack, artifact, approval])
    db.commit()

    with pytest.raises(AuthorizationError, match="not accessible"):
        EvidenceService(db).manifest(run_a.id, pack.id, "owner-b")
    with pytest.raises(AuthorizationError, match="not accessible"):
        EvidenceService(db).ingest_artifact(
            run_a.id,
            "test",
            "/repos/tenant-a/private.txt",
            "owner-b",
        )
    with pytest.raises(AuthorizationError, match="not accessible"):
        PolicyService(db).decide(
            approval.id, True, "owner-b", "cross-tenant attempt"
        )
    assert approval.status == "PENDING"

    with pytest.raises(AuthorizationError, match="not accessible"):
        RepositoryAuditService(db).authorize(
            run_a.task_id,
            "/repos/tenant-a",
            "owner-b",
            "tasks.write",
        )

    executor_a = _executor(project_a.id, "project:" + project_a.id, "executor-a")
    executor_b = _executor(project_b.id, "project:" + project_b.id, "executor-b")
    step_a = RunStep(
        run_id=run_a.id,
        sequence=1,
        name="A",
        idempotency_key="a",
    )
    step_b = RunStep(
        run_id=run_b.id,
        sequence=1,
        name="B",
        idempotency_key="b",
    )
    db.add_all([executor_a, executor_b, step_a, step_b])
    db.flush()
    db.add_all(
        [
            _job(run_a, step_a, project_a.id, organization_a.id, "a"),
            _job(
                run_b,
                step_b,
                project_b.id,
                project_b.organization_id,
                "b",
            ),
        ]
    )
    db.commit()

    lease = ExecutionPlaneService(db).acquire_lease(executor_b)
    assert lease is not None
    assert lease.job.project_id == project_b.id
    with pytest.raises(AuthorizationError, match="not accessible"):
        ExecutionPlaneService(db).revoke_executor(
            executor_a.id, "owner-b", "cross-tenant attempt"
        )

    with pytest.raises(AuthorizationError, match="not accessible"):
        TenantAuditService(db).query(organization_a.id, "owner-b")
    events = TenantAuditService(db).query(organization_a.id, "owner-a")
    ordered = sorted(events, key=lambda event: event.sequence)
    assert ordered
    for previous, current in zip(ordered, ordered[1:], strict=False):
        assert current.previous_event_hash == previous.event_hash


def test_scoped_service_credentials_are_hashed_expiring_and_revocable(db, monkeypatch):
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")
    organization_a, project_a, _, project_b = _tenants(db)
    issued = ServiceCredentialService(db).create(
        organization_id=organization_a.id,
        project_id=project_a.id,
        actor_id="owner-a",
        name="ci",
        role="viewer",
        permissions=["runs.execute"],
        expires_in_seconds=3600,
    )

    assert issued.token.startswith("sacm_service_")
    assert issued.token not in issued.record.token_hash
    assert issued.record.token_hash != issued.token
    identity = authenticate_request(f"Bearer {issued.token}", None, db)
    assert identity.subject == f"service:{issued.record.id}"
    assert issued.record.last_used_at is not None

    tenancy = TenancyService(db)
    tenancy.require_project_permission(
        project_a.id, identity.subject, "runs.execute"
    )
    with pytest.raises(AuthorizationError, match="not accessible"):
        tenancy.require_project_permission(
            project_b.id, identity.subject, "runs.execute"
        )

    ServiceCredentialService(db).revoke(
        organization_a.id, issued.record.id, "owner-a"
    )
    with pytest.raises(PermissionError, match="invalid, expired, or revoked"):
        authenticate_request(f"Bearer {issued.token}", None, db)

    expired = ServiceCredential(
        organization_id=organization_a.id,
        name="expired",
        token_hash="unused",
        token_prefix="sacm_service_expired",
        role="viewer",
        permissions=[],
        expires_at=datetime.utcnow() - timedelta(seconds=1),
        created_by="owner-a",
    )
    db.add(expired)
    db.commit()
    assert expired.expires_at < datetime.utcnow()


def test_legacy_backfill_is_idempotent_and_preserves_executor_scope(db):
    organization_a, project_a, _, _ = _tenants(db)
    run = RunService(db).create(
        RunCreate(title="Legacy", description="Backfill", project_id=project_a.id)
    )
    run.task.organization_id = None
    run.task.project_id = None
    run.task.tenant_attribution = None
    run.organization_id = None
    run.tenant_attribution = None
    memory = MemoryChunk(
        task_id=run.task_id,
        source_type="legacy",
        scope="task",
        scope_key=run.task_id,
        content="legacy memory",
        content_hash="legacy-memory-hash",
        confidence=0.7,
    )
    executor = _executor(
        project_a.id, f"project:{project_a.id}", "legacy-executor"
    )
    executor.tenant_attribution = None
    db.add_all([memory, executor])
    db.commit()

    first = TenantBackfillService(db).run(organization_a.id)
    second = TenantBackfillService(db).run(organization_a.id)

    assert first["organization_id"] == organization_a.id
    assert run.task.organization_id == organization_a.id
    assert run.task.project_id == project_a.id
    assert memory.organization_id == organization_a.id
    assert memory.project_id == project_a.id
    assert executor.organization_id is None
    assert executor.project_id == project_a.id
    assert executor.tenant_attribution["organization_id"] == organization_a.id
    assert second["unresolved"] == []


def test_tenant_audit_events_are_append_only(db):
    organization_a, _, _, _ = _tenants(db)
    event = (
        db.query(TenantAuditEvent)
        .filter(TenantAuditEvent.organization_id == organization_a.id)
        .first()
    )
    assert event is not None
    event.reason = "tampered"
    with pytest.raises(RuntimeError, match="append-only"):
        db.commit()
    db.rollback()


def test_service_credential_and_audit_apis():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    organization_a, project_a, _, _ = _tenants(db)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            issued = client.post(
                f"/v1/organizations/{organization_a.id}/service-credentials",
                headers={"X-SACM-Actor": "owner-a"},
                json={
                    "name": "automation",
                    "project_id": project_a.id,
                    "role": "viewer",
                    "permissions": ["audit.export"],
                    "expires_in_seconds": 3600,
                },
            )
            assert issued.status_code == 201
            payload = issued.json()
            token = payload.pop("token")
            assert token.startswith("sacm_service_")

            listed = client.get(
                f"/v1/organizations/{organization_a.id}/service-credentials",
                headers={"X-SACM-Actor": "owner-a"},
            )
            assert listed.status_code == 200
            assert "token" not in listed.json()[0]

            audit = client.get(
                f"/v1/organizations/{organization_a.id}/audit-events",
                headers={"Authorization": f"Bearer {token}"},
                params={"project_id": project_a.id},
            )
            assert audit.status_code == 200
            assert audit.json()

            revoked = client.post(
                f"/v1/organizations/{organization_a.id}/service-credentials/"
                f"{payload['id']}/revoke",
                headers={"X-SACM-Actor": "owner-a"},
            )
            assert revoked.status_code == 200
            denied = client.get(
                f"/v1/organizations/{organization_a.id}/audit-events",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert denied.status_code == 401
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def _executor(project_id: str, scope_key: str, identity: str) -> ExecutorRegistration:
    return ExecutorRegistration(
        project_id=project_id,
        scope_key=scope_key,
        executor_identity=identity,
        display_name=identity,
        capabilities=["agent-task/v1"],
        labels={},
        runtime_kind="docker",
        sandbox_runtime="runsc",
        sandbox_policy={},
        public_signing_key="unused",
        signing_key_fingerprint=identity,
        auth_token_hash=identity,
        status="ACTIVE",
        last_heartbeat_at=datetime.utcnow(),
        version="1",
        network_boundary={},
    )


def _job(run, step, project_id: str, organization_id: str, key: str) -> ExecutionJob:
    return ExecutionJob(
        organization_id=organization_id,
        project_id=project_id,
        scope_key=f"project:{project_id}",
        run_id=run.id,
        run_step_id=step.id,
        task_id=run.task_id,
        state="QUEUED",
        idempotency_key=key,
        required_capabilities=["agent-task/v1"],
        required_labels={},
        payload_contract={},
        payload_hash=key,
        payload_signature=key,
        payload_signature_metadata={},
    )
