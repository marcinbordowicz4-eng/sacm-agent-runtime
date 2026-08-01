import base64
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.execution_plane_service import (
    ExecutionAuthorizationError,
    ExecutionConflict,
    ExecutionPlaneService,
    ExecutorAuthenticationError,
    _utcnow,
)
from sacm.core.execution_signing import (
    canonical_hash,
    canonical_json,
    reset_signing_key_cache,
    sign_control_plane_payload,
)
from sacm.core.external_agent_service import ExternalAgentService
from sacm.core.recovery_service import RecoveryService
from sacm.core.run_service import RunService
from sacm.core.snapshot_service import SnapshotService
from sacm.core.tenancy_service import TenancyService
from sacm.core.workflow_backend import RemoteWorkflowBackend
from sacm.infrastructure.db.models import Base, ExecutionJob
from sacm.infrastructure.db.session import get_db
from sacm.schemas.contracts import AgentResultV1, ExternalAgentStepCreate
from sacm.schemas.execution_plane import (
    ExecutorEnroll,
    ExecutorEnrollmentTokenCreate,
    SandboxPolicyV1,
    SignedJobResult,
)
from sacm.schemas.run import RunCreate


def _keys() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_key.decode()


def _project(db, owner: str, slug: str):
    tenancy = TenancyService(db)
    organization = tenancy.create_organization(slug, slug.title(), owner)
    project = tenancy.create_project(
        organization.id,
        "runtime",
        "Runtime",
        owner,
        repository_path=f"/repos/{slug}",
    )
    return organization, project


def _executor(db, project_id: str, owner: str, identity: str, capabilities=None):
    private_key, public_key = _keys()
    service = ExecutionPlaneService(db)
    issued = service.issue_enrollment_token(
        ExecutorEnrollmentTokenCreate(project_id=project_id), owner
    )
    enrolled = service.enroll(
        ExecutorEnroll(
            enrollment_token=issued.token,
            executor_identity=identity,
            display_name=identity,
            capabilities=capabilities or ["agent-task/v1"],
            labels={"region": "eu"},
            runtime_kind="docker",
            sandbox_runtime="runsc",
            public_signing_key=public_key,
            version="1.2.3",
            network_boundary={"egress": "restricted"},
        )
    )
    return enrolled.executor, enrolled.auth_token, private_key


def _job(db, project_id: str):
    run = RunService(db).create(
        RunCreate(
            title="Remote",
            description="Execute remotely.",
            project_id=project_id,
        )
    )
    RunService(db).transition(run.id, "PLANNING", "RemoteRunSubmitted")
    scheduled = ExternalAgentService(db).schedule(
        run.id,
        ExternalAgentStepCreate(
            framework="sacm-remote",
            agent_name="test-executor",
            idempotency_key=f"{run.id}:remote",
            role="reasoner",
            objective="Execute the task.",
            token_budget=100,
            timeout_seconds=60,
        ),
    )
    job = ExecutionPlaneService(db).schedule(
        run_id=run.id,
        run_step_id=scheduled.step.id,
        task=scheduled.task,
        idempotency_key=f"{run.id}:remote",
        required_capabilities=["agent-task/v1"],
        required_labels={"region": "eu"},
    )
    return run, scheduled.step, job


def _signed(private_key, executor, job, lease_token, result):
    data = result.model_dump(mode="json")
    return SignedJobResult(
        lease_token=lease_token,
        result=result,
        result_hash=canonical_hash(data),
        signature=base64.b64encode(
            private_key.sign(canonical_json(data))
        ).decode(),
        signing_key_fingerprint=executor.signing_key_fingerprint,
    )


def test_capability_lease_and_idempotent_signed_completion(db):
    _, project = _project(db, "owner", "acme")
    executor, _, private_key = _executor(db, project.id, "owner", "exec-1")
    _, step, job = _job(db, project.id)
    service = ExecutionPlaneService(db)

    lease = service.acquire_lease(executor)
    assert lease is not None
    assert lease.job.id == job.id
    assert lease.job.lease_token_hash
    assert lease.lease_token not in lease.job.lease_token_hash
    service.start_job(executor, job.id, lease.lease_token)
    result = AgentResultV1(
        run_id=job.run_id,
        step_id=job.run_step_id,
        status="COMPLETED",
        summary="Remote execution completed.",
        confidence=0.9,
    )
    submission = _signed(private_key, executor, job, lease.lease_token, result)

    completed = service.complete_job(executor, job.id, submission)
    repeated = service.complete_job(executor, job.id, submission)

    assert completed.state == repeated.state == "COMPLETED"
    assert completed.result_hash == submission.result_hash
    assert RunService(db).get_step(job.run_id, step.id).status == "COMPLETED"
    reasons = {
        snapshot.creation_reason
        for snapshot in SnapshotService(db).list_snapshots(job.run_id)
    }
    assert f"execution_job_queued:{job.id}" in reasons
    assert f"execution_job_completed:{job.id}" in reasons


def test_remote_completion_persists_and_retries_analytics_failure(db, monkeypatch):
    _, project = _project(db, "owner", "acme")
    executor, _, private_key = _executor(db, project.id, "owner", "exec-1")
    _, _, job = _job(db, project.id)
    service = ExecutionPlaneService(db)
    lease = service.acquire_lease(executor)
    assert lease is not None
    service.start_job(executor, job.id, lease.lease_token)
    result = AgentResultV1(
        run_id=job.run_id,
        step_id=job.run_step_id,
        status="COMPLETED",
        summary="Remote execution completed.",
        confidence=0.9,
    )
    submission = _signed(private_key, executor, job, lease.lease_token, result)
    outcomes = iter(["analytics unavailable", None])
    monkeypatch.setattr(
        ExternalAgentService,
        "refresh_analytics",
        lambda *_: next(outcomes),
    )

    completed = service.complete_job(executor, job.id, submission)
    assert completed.result_signature_metadata["analytics_status"] == "FAILED"
    assert (
        completed.result_signature_metadata["analytics_error"]
        == "analytics unavailable"
    )

    repeated = service.complete_job(executor, job.id, submission)
    assert repeated.result_signature_metadata["analytics_status"] == "COMPLETED"
    assert "analytics_error" not in repeated.result_signature_metadata


def test_failed_remote_job_queues_classified_recovery(db):
    _, project = _project(db, "owner", "acme")
    executor, _, private_key = _executor(db, project.id, "owner", "exec-1")
    run, step, job = _job(db, project.id)
    service = ExecutionPlaneService(db)
    lease = service.acquire_lease(executor)
    assert lease is not None
    service.start_job(executor, job.id, lease.lease_token)
    result = AgentResultV1(
        run_id=job.run_id,
        step_id=job.run_step_id,
        status="FAILED",
        summary="Compilation failed.",
        failure={
            "classification": "COMPILATION",
            "type": "CompilerError",
            "message": "Type checking failed.",
        },
    )

    failed = service.fail_job(
        executor,
        job.id,
        _signed(private_key, executor, job, lease.lease_token, result),
    )

    queued = (
        db.query(ExecutionJob)
        .filter(ExecutionJob.run_id == run.id, ExecutionJob.state == "QUEUED")
        .one()
    )
    assert failed.state == "FAILED"
    assert queued.id != failed.id
    assert queued.run_step_id == step.id
    assert queued.payload_contract["execution_context"]["recovery"]["decision"][
        "action"
    ] == "REPAIR_CODE"
    assert RunService(db).get(run.id).status == "FIXING"


def test_failed_remote_job_recovery_rolls_back_when_requeue_fails(db, monkeypatch):
    _, project = _project(db, "owner", "acme")
    executor, _, private_key = _executor(db, project.id, "owner", "exec-1")
    run, step, job = _job(db, project.id)
    service = ExecutionPlaneService(db)
    lease = service.acquire_lease(executor)
    assert lease is not None
    service.start_job(executor, job.id, lease.lease_token)
    result = AgentResultV1(
        run_id=job.run_id,
        step_id=job.run_step_id,
        status="FAILED",
        summary="Compilation failed.",
        failure={
            "classification": "COMPILATION",
            "type": "CompilerError",
            "message": "Type checking failed.",
        },
    )

    def fail_schedule(**kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(service, "schedule", fail_schedule)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        service.fail_job(
            executor,
            job.id,
            _signed(private_key, executor, job, lease.lease_token, result),
        )

    db.expire_all()
    assert db.get(ExecutionJob, job.id).state == "RUNNING"
    assert RunService(db).get_step(run.id, step.id).status == "RUNNING"
    assert RunService(db).get(run.id).recovery_attempt_count == 0


def test_remote_backend_reschedules_existing_recovery_step(db):
    _, project = _project(db, "owner", "acme")
    run, step, job = _job(db, project.id)
    runs = RunService(db)
    runs.start_step(run.id, step.id)
    runs.fail_step(
        run.id,
        step.id,
        {"type": "ToolError", "message": "temporary tool failure"},
    )
    RecoveryService(db).handle(
        run.id,
        step.id,
        {"type": "ToolError", "message": "temporary tool failure"},
    )
    job.state = "FAILED"
    db.commit()

    result = RemoteWorkflowBackend(db).execute(run.id)

    recovery_job = db.get(ExecutionJob, result["job_id"])
    assert recovery_job.run_step_id == step.id
    assert recovery_job.idempotency_key.endswith(":recovery:1")
    assert recovery_job.payload_contract["execution_context"]["recovery"]["decision"][
        "action"
    ] == "RETRY"


def test_remote_backend_rejects_escalated_recovery(db, monkeypatch):
    monkeypatch.setenv("SACM_MAX_RECOVERY_ATTEMPTS", "0")
    _, project = _project(db, "owner", "acme")
    run, step, job = _job(db, project.id)
    runs = RunService(db)
    runs.start_step(run.id, step.id)
    runs.fail_step(
        run.id,
        step.id,
        {"type": "ToolError", "message": "persistent tool failure"},
    )
    RecoveryService(db).handle(
        run.id,
        step.id,
        {"type": "ToolError", "message": "persistent tool failure"},
    )
    job.state = "FAILED"
    db.commit()

    with pytest.raises(ValueError, match="requires an explicit recovery"):
        RemoteWorkflowBackend(db).execute(run.id)


def test_wrong_executor_token_hash_and_signature_tampering_are_rejected(db):
    _, project = _project(db, "owner", "acme")
    executor, _, private_key = _executor(db, project.id, "owner", "exec-1")
    other, _, _ = _executor(db, project.id, "owner", "exec-2")
    _, _, job = _job(db, project.id)
    service = ExecutionPlaneService(db)
    lease = service.acquire_lease(executor)
    assert lease is not None

    with pytest.raises(ExecutionAuthorizationError):
        service.start_job(other, job.id, lease.lease_token)
    with pytest.raises(ExecutorAuthenticationError):
        service.start_job(executor, job.id, "wrong-token" * 8)

    result = AgentResultV1(
        run_id=job.run_id,
        step_id=job.run_step_id,
        status="COMPLETED",
        summary="done",
    )
    submission = _signed(private_key, executor, job, lease.lease_token, result)
    tampered_hash = submission.model_copy(
        update={"result_hash": "0" * 64}
    )
    with pytest.raises(ExecutionConflict, match="hash"):
        service.complete_job(executor, job.id, tampered_hash)
    tampered_signature = submission.model_copy(
        update={"signature": base64.b64encode(b"invalid").decode()}
    )
    with pytest.raises(ExecutionConflict, match="signature"):
        service.complete_job(executor, job.id, tampered_signature)


def test_expired_lease_recovery_and_revocation(db):
    _, project = _project(db, "owner", "acme")
    executor, auth_token, _ = _executor(db, project.id, "owner", "exec-1")
    _, _, job = _job(db, project.id)
    service = ExecutionPlaneService(db)
    lease = service.acquire_lease(executor, lease_seconds=15)
    assert lease is not None
    lease.job.lease_expires_at = _utcnow() - timedelta(seconds=1)
    db.commit()

    recovered = service.recover_expired()
    assert recovered[0].state == "QUEUED"
    new_lease = service.acquire_lease(executor)
    assert new_lease is not None
    assert new_lease.lease_token != lease.lease_token

    revoked = service.revoke_executor(executor.id, "owner", "compromised")
    assert revoked.status == "REVOKED"
    assert db.get(ExecutionJob, job.id).state == "QUEUED"
    with pytest.raises(ExecutorAuthenticationError):
        service.authenticate_executor(auth_token)


def test_stale_executor_becomes_offline_until_heartbeat(db, monkeypatch):
    monkeypatch.setenv("SACM_EXECUTOR_OFFLINE_SECONDS", "60")
    _, project = _project(db, "owner", "acme")
    executor, auth_token, _ = _executor(db, project.id, "owner", "exec-1")
    executor.last_heartbeat_at = _utcnow() - timedelta(seconds=61)
    db.commit()
    service = ExecutionPlaneService(db)

    authenticated = service.authenticate_executor(auth_token)
    assert authenticated.status == "OFFLINE"
    with pytest.raises(ExecutorAuthenticationError):
        service.acquire_lease(authenticated)

    refreshed = service.heartbeat_executor(authenticated)
    assert refreshed.status == "ACTIVE"


def test_tenant_and_capability_isolation(db):
    _, project_a = _project(db, "owner-a", "alpha")
    _, project_b = _project(db, "owner-b", "bravo")
    executor_a, _, _ = _executor(
        db, project_a.id, "owner-a", "exec-a", capabilities=["different"]
    )
    executor_b, _, _ = _executor(db, project_b.id, "owner-b", "exec-b")
    _job(db, project_a.id)
    _, _, job_b = _job(db, project_b.id)
    service = ExecutionPlaneService(db)

    assert service.acquire_lease(executor_a) is None
    lease_b = service.acquire_lease(executor_b)
    assert lease_b is not None
    assert lease_b.job.id == job_b.id


def test_competing_executors_can_only_claim_a_job_once(db):
    _, project = _project(db, "owner", "acme")
    executor_a, _, _ = _executor(db, project.id, "owner", "exec-a")
    executor_b, _, _ = _executor(db, project.id, "owner", "exec-b")
    _, _, job = _job(db, project.id)

    lease_a = ExecutionPlaneService(db).acquire_lease(executor_a)
    lease_b = ExecutionPlaneService(db).acquire_lease(executor_b)

    assert lease_a is not None
    assert lease_a.job.id == job.id
    assert lease_b is None


def test_remote_backend_schedules_instead_of_running_local_workflow(db):
    _, project = _project(db, "owner", "acme")
    run = RunService(db).create(
        RunCreate(
            title="Remote",
            description="Schedule the task.",
            project_id=project.id,
        )
    )

    result = RemoteWorkflowBackend(db).execute(run.id)

    assert result["backend"] == "remote"
    assert result["job_state"] == "QUEUED"
    assert db.get(ExecutionJob, result["job_id"]).payload_contract[
        "schema_version"
    ] == "agent-task/v1"


def test_run_cancellation_invalidates_queued_or_leased_jobs(db):
    _, project = _project(db, "owner", "acme")
    executor, _, _ = _executor(db, project.id, "owner", "exec-1")
    run, _, job = _job(db, project.id)
    lease = ExecutionPlaneService(db).acquire_lease(executor)
    assert lease is not None

    RunService(db).cancel(run.id)

    cancelled = db.get(ExecutionJob, job.id)
    assert cancelled.state == "CANCELLED"
    assert cancelled.lease_token_hash is None


def test_production_enrollment_requires_verified_approved_sandbox(
    db, monkeypatch
):
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")
    monkeypatch.setenv("SACM_APPROVED_SANDBOX_RUNTIMES", "runsc")
    control_private, _ = _keys()
    monkeypatch.setenv(
        "SACM_JOB_SIGNING_PRIVATE_KEY",
        control_private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    reset_signing_key_cache()
    _, signing_metadata = sign_control_plane_payload(
        {"schema_version": "test-key-proof/v1"}
    )
    _, project = _project(db, "owner", "acme")
    _, public_key = _keys()
    service = ExecutionPlaneService(db)
    issued = service.issue_enrollment_token(
        ExecutorEnrollmentTokenCreate(project_id=project.id), "owner"
    )
    payload = ExecutorEnroll(
        enrollment_token=issued.token,
        executor_identity="exec-1",
        display_name="Executor",
        capabilities=["agent-task/v1"],
        runtime_kind="docker",
        sandbox_runtime="runsc",
        public_signing_key=public_key,
        version="1.0",
    )

    with pytest.raises(ExecutionConflict, match="sandbox-policy"):
        service.enroll(payload)

    enrolled = service.enroll(
        payload.model_copy(
            update={
                "sandbox_policy": SandboxPolicyV1(
                    runtime="runsc",
                    host_runtime_verified=True,
                    verification_command=(
                        "command -v runsc && runsc --version && "
                        "docker run --runtime=runsc alpine:3.20 true"
                    ),
                    isolation="user-space-kernel",
                    network_mode="deny-by-default",
                ),
                "network_boundary": {
                    "schema_version": "executor-network-boundary/v1",
                    "deployment_type": "on-premises",
                    "boundary_id": "acme-production",
                    "residency_region": "eu",
                    "outbound_allowlist": [],
                    "metadata_service_blocked": True,
                    "tls": {
                        "server_certificate_sha256": "a" * 64,
                        "signing_key_sha256": signing_metadata["key_fingerprint"],
                    },
                },
                "storage_region": "eu",
            }
        )
    )
    assert enrolled.executor.sandbox_runtime == "runsc"


def test_executor_network_boundary_honors_tenant_policy_and_residency(db):
    organization, project = _project(db, "owner", "acme")
    organization.governance_metadata = {
        "executor_network_policy": {
            "allowed_boundary_types": ["vpc"],
            "allowed_regions": ["eu-west"],
            "allowed_outbound_hosts": ["control.internal"],
            "require_proxy": True,
            "control_plane_tls_fingerprints": ["a" * 64],
        }
    }
    project.data_region = "eu-west"
    db.commit()
    _, public_key = _keys()
    service = ExecutionPlaneService(db)
    issued = service.issue_enrollment_token(
        ExecutorEnrollmentTokenCreate(project_id=project.id), "owner"
    )
    payload = ExecutorEnroll(
        enrollment_token=issued.token,
        executor_identity="policy-executor",
        display_name="Policy executor",
        capabilities=["agent-task/v1"],
        runtime_kind="customer-hosted",
        sandbox_runtime="runsc",
        public_signing_key=public_key,
        version="1.0.0",
        storage_region="eu-west",
        network_boundary={
            "schema_version": "executor-network-boundary/v1",
            "deployment_type": "vnet",
            "boundary_id": "boundary",
            "residency_region": "us-east",
            "outbound_allowlist": ["metadata.google.internal"],
            "metadata_service_blocked": False,
            "tls": {"server_certificate_sha256": "b" * 64},
        },
    )

    with pytest.raises(ExecutionConflict, match="metadata|residency|boundary"):
        service.enroll(payload)

    enrolled = service.enroll(
        payload.model_copy(
            update={
                "network_boundary": {
                    "schema_version": "executor-network-boundary/v1",
                    "deployment_type": "vpc",
                    "boundary_id": "boundary",
                    "residency_region": "eu-west",
                    "outbound_allowlist": ["control.internal"],
                    "proxy_url": "https://proxy.internal",
                    "metadata_service_blocked": True,
                    "tls": {"server_certificate_sha256": "a" * 64},
                }
            }
        )
    )
    assert enrolled.executor.storage_region == "eu-west"


def test_executor_rotation_capacity_health_and_version_drain(db, monkeypatch):
    _, project = _project(db, "owner", "acme")
    executor, auth_token, _ = _executor(db, project.id, "owner", "exec-1")
    service = ExecutionPlaneService(db)
    service.heartbeat_executor(
        executor,
        capabilities=["agent-task/v1", "evidence/v1"],
        labels={"region": "eu", "tier": "isolated"},
        capacity={
            "max_concurrent_jobs": 4,
            "active_jobs": 1,
            "memory_mb": 4096,
            "draining": False,
        },
    )
    health = service.fleet_health([executor])
    assert health["capacity"]["available_slots"] == 3
    assert health["slo"]["met"] is True

    monkeypatch.setenv("SACM_EXECUTOR_MINIMUM_VERSION", "9.0.0")
    directives = service.control_directives(executor)
    assert directives["drain"] is True
    assert directives["automatic_update"] is False
    assert directives["update_manifest"]["manifest_hash"]

    _, new_public_key = _keys()
    from sacm.core.execution_signing import public_key_fingerprint
    from sacm.schemas.execution_plane import ExecutorRotate

    rotated = service.rotate_executor_identity(
        executor,
        ExecutorRotate(
            public_signing_key=new_public_key,
            signing_key_fingerprint=public_key_fingerprint(new_public_key),
        ),
    )
    assert rotated.auth_token != auth_token
    with pytest.raises(ExecutorAuthenticationError):
        service.authenticate_executor(auth_token)


def test_executor_control_and_service_apis_are_authenticated():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    _, project = _project(db, "owner", "acme")
    _, public_key = _keys()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            issued = client.post(
                "/v1/executors/enrollment-tokens",
                headers={"X-SACM-Actor": "owner"},
                json={"project_id": project.id},
            )
            assert issued.status_code == 201
            enrolled = client.post(
                "/v1/executors/enroll",
                json={
                    "enrollment_token": issued.json()["enrollment_token"],
                    "executor_identity": "api-executor",
                    "display_name": "API Executor",
                    "capabilities": ["agent-task/v1"],
                    "runtime_kind": "docker",
                    "sandbox_runtime": "runsc",
                    "public_signing_key": public_key,
                    "version": "1.0",
                },
            )
            assert enrolled.status_code == 201
            executor_id = enrolled.json()["executor"]["id"]
            auth_token = enrolled.json()["auth_token"]
            assert client.post(
                "/v1/executor/jobs/lease",
                headers={"Authorization": f"Bearer {auth_token}"},
                json={},
            ).status_code == 204
            listed = client.get(
                "/v1/executors",
                headers={"X-SACM-Actor": "owner"},
                params={"project_id": project.id},
            )
            assert listed.status_code == 200
            assert [item["id"] for item in listed.json()] == [executor_id]
            revoked = client.post(
                f"/v1/executors/{executor_id}/revoke",
                headers={"X-SACM-Actor": "owner"},
                json={"reason": "rotation"},
            )
            assert revoked.status_code == 200
            assert revoked.json()["status"] == "REVOKED"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
