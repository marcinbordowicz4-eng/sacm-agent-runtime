import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.routes.approvals import _authorize_approval_run
from apps.api.routes.runs import _authorize_run
from sacm.core.auth_service import (
    require_direct_action_api_enabled,
    require_legacy_api_enabled,
    validate_production_configuration,
)
from sacm.core.run_service import RunService
from sacm.core.tenancy_service import TenancyService
from sacm.schemas.run import RunCreate


def _production_environment(monkeypatch):
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")
    monkeypatch.setenv("SACM_AUTH_REQUIRED", "true")
    monkeypatch.setenv("SACM_OIDC_ISSUER", "https://issuer.example")
    monkeypatch.setenv("SACM_OIDC_AUDIENCE", "sacm")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://example")
    monkeypatch.setenv("SACM_OPA_URL", "https://opa.example")
    monkeypatch.setenv("SACM_OPA_FAIL_CLOSED", "true")
    monkeypatch.setenv("SACM_EVIDENCE_HMAC_KEY", "test-key")
    monkeypatch.setenv(
        "SACM_AUDIT_EXPORT_SIGNING_PRIVATE_KEY_FILE",
        "/run/secrets/audit_export_signing_key",
    )
    monkeypatch.setenv("SACM_LEGACY_API_ENABLED", "false")
    monkeypatch.setenv("SACM_DIRECT_ACTION_API_ENABLED", "false")
    monkeypatch.setenv("SACM_WORKFLOW_BACKEND", "remote")
    monkeypatch.setenv(
        "SACM_JOB_SIGNING_PRIVATE_KEY_FILE", "/run/secrets/job_signing_key"
    )
    monkeypatch.setenv("SACM_APPROVED_SANDBOX_RUNTIMES", "runsc")
    monkeypatch.setenv("SACM_SECRET_PROVIDER", "vault")
    monkeypatch.setenv("SACM_APPROVED_SECRET_PROVIDERS", "vault")
    monkeypatch.setenv("SACM_BACKUP_ROOT", "/backups")
    monkeypatch.setenv(
        "SACM_BACKUP_AGE_RECIPIENTS_FILE", "/run/secrets/backup_recipients"
    )
    monkeypatch.setenv(
        "SACM_BACKUP_AGE_IDENTITY_FILE", "/run/secrets/backup_identity"
    )
    monkeypatch.setenv("SACM_BACKUP_ENCRYPTION_KEY_ID", "backup-v1")
    monkeypatch.setenv(
        "SACM_DESTRUCTIVE_RESTORE_GUARD_FILE", "/run/secrets/restore_guard"
    )


def test_production_configuration_rejects_missing_controls(monkeypatch):
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="SACM_AUTH_REQUIRED=true"):
        validate_production_configuration()


def test_production_configuration_accepts_required_controls(monkeypatch):
    _production_environment(monkeypatch)

    validate_production_configuration()


def test_production_configuration_rejects_local_execution(monkeypatch):
    _production_environment(monkeypatch)
    monkeypatch.setenv("SACM_WORKFLOW_BACKEND", "local")

    with pytest.raises(RuntimeError, match="SACM_WORKFLOW_BACKEND"):
        validate_production_configuration()


def test_production_configuration_rejects_environment_secret_broker(
    monkeypatch,
):
    _production_environment(monkeypatch)
    monkeypatch.setenv("SACM_SECRET_PROVIDER", "environment")

    with pytest.raises(RuntimeError, match="non-environment"):
        validate_production_configuration()


def test_production_configuration_requires_evidence_signing(monkeypatch):
    _production_environment(monkeypatch)
    monkeypatch.delenv("SACM_EVIDENCE_HMAC_KEY", raising=False)
    monkeypatch.delenv("SACM_EVIDENCE_HMAC_KEY_FILE", raising=False)
    monkeypatch.delenv("SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE", raising=False)

    with pytest.raises(RuntimeError, match="EVIDENCE_SIGNING_PRIVATE_KEY_FILE"):
        validate_production_configuration()


def test_production_image_is_labeled_non_root_and_mounts_evidence_key():
    dockerfile = open("Dockerfile", encoding="utf-8").read()
    compose = open("docker-compose.production.yml", encoding="utf-8").read()

    assert "org.opencontainers.image.revision" in dockerfile
    assert "USER sacm" in dockerfile
    assert "SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE" in compose
    assert "evidence_signing_private_key" in compose


def test_production_disables_legacy_and_direct_action_apis(monkeypatch):
    _production_environment(monkeypatch)

    with pytest.raises(HTTPException, match="legacy API"):
        require_legacy_api_enabled()
    with pytest.raises(HTTPException, match="Direct action APIs"):
        require_direct_action_api_enabled()


def test_legacy_and_direct_action_endpoints_require_an_authenticated_actor():
    with TestClient(app) as client:
        assert client.get("/tasks/not-a-real-task").status_code == 401
        assert client.post(
            "/github/issues",
            json={"repo_path": "/tmp/repository", "title": "x", "body": "x"},
        ).status_code == 401


def test_executor_endpoints_never_use_development_actor_identity():
    with TestClient(app) as client:
        response = client.post(
            "/v1/executor/jobs/lease",
            headers={"X-SACM-Actor": "forged-executor"},
            json={},
        )

    assert response.status_code == 401
    assert "Executor bearer token" in response.json()["detail"]


def test_run_and_approval_authorization_require_project_membership(db, monkeypatch):
    _production_environment(monkeypatch)
    tenancy = TenancyService(db)
    organization = tenancy.create_organization("acme", "Acme", "owner")
    project = tenancy.create_project(
        organization.id, "runtime", "Runtime", "owner", repository_path="/repos/runtime"
    )
    tenancy.add_member(organization.id, "owner", "developer", "developer")
    run = RunService(db).create(
        RunCreate(
            title="Protected run",
            description="Authorization test.",
            project_id=project.id,
            target_repo_path=project.repository_path,
        )
    )

    assert _authorize_run(db, run.id, "developer").id == run.id
    _authorize_approval_run(db, run.id, "owner", "admin")

    with pytest.raises(HTTPException, match="Insufficient organization role"):
        _authorize_approval_run(db, run.id, "developer", "admin")
    with pytest.raises(HTTPException, match="Insufficient organization role"):
        _authorize_run(db, run.id, "unknown")
