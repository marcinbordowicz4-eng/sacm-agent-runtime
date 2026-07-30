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
    monkeypatch.setenv("SACM_LEGACY_API_ENABLED", "false")
    monkeypatch.setenv("SACM_DIRECT_ACTION_API_ENABLED", "false")


def test_production_configuration_rejects_missing_controls(monkeypatch):
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="SACM_AUTH_REQUIRED=true"):
        validate_production_configuration()


def test_production_configuration_accepts_required_controls(monkeypatch):
    _production_environment(monkeypatch)

    validate_production_configuration()


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
