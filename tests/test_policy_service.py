from datetime import datetime, timedelta, timezone

import pytest

from sacm.core.policy_service import ApprovalRequiredError, PolicyService, ToolGateway
from sacm.core.run_service import RunService
from sacm.schemas.run import RunCreate


def _run_id(db) -> str:
    return RunService(db).create(
        RunCreate(title="Policy test", description="Exercise a protected action.")
    ).id


def test_local_policy_denies_configured_actions(monkeypatch):
    monkeypatch.setenv("SACM_DENIED_TOOL_ACTIONS", "github.merge")

    decision = PolicyService().evaluate("github.merge", {"pull_request": 1})

    assert decision.allowed is False
    assert "denied" in decision.reason


def test_policy_creates_and_honors_durable_approval(db, monkeypatch):
    monkeypatch.setenv("SACM_APPROVAL_REQUIRED_ACTIONS", "github.create_draft_pr")
    run_id = _run_id(db)
    policy = PolicyService(db)
    gateway = ToolGateway(policy)
    resource = {"repository": "owner/repository", "branch": "sacm/test"}

    with pytest.raises(ApprovalRequiredError) as error:
        gateway.authorize("github.create_draft_pr", resource, run_id=run_id)

    pending = error.value.approval
    assert pending.status == "PENDING"
    approved = policy.decide(pending.id, True, "maintainer", "Reviewed change plan.")
    assert approved.status == "APPROVED"

    gateway.authorize("github.create_draft_pr", resource, run_id=run_id)


def test_changed_resource_cannot_reuse_an_approved_decision(db, monkeypatch):
    monkeypatch.setenv("SACM_APPROVAL_REQUIRED_ACTIONS", "github.create_draft_pr")
    run_id = _run_id(db)
    policy = PolicyService(db)
    gateway = ToolGateway(policy)
    approved_resource = {
        "repository": "owner/repository",
        "branch": "sacm/approved-plan",
        "plan_source_hash": "a" * 64,
    }

    with pytest.raises(ApprovalRequiredError) as initial:
        gateway.authorize("github.create_draft_pr", approved_resource, run_id=run_id)
    approved = policy.decide(
        initial.value.approval.id, True, "maintainer", "Plan revision reviewed."
    )

    changed_resource = {**approved_resource, "plan_source_hash": "b" * 64}
    with pytest.raises(ApprovalRequiredError) as changed:
        gateway.authorize("github.create_draft_pr", changed_resource, run_id=run_id)

    assert approved.status == "APPROVED"
    assert changed.value.approval.id != approved.id
    assert changed.value.approval.status == "PENDING"
    assert changed.value.approval.resource_digest != approved.resource_digest


def test_expired_approval_is_replaced_with_a_new_pending_request(db, monkeypatch):
    monkeypatch.setenv("SACM_APPROVAL_REQUIRED_ACTIONS", "workspace.execute")
    monkeypatch.setenv("SACM_APPROVAL_TTL_SECONDS", "60")
    run_id = _run_id(db)
    policy = PolicyService(db)
    gateway = ToolGateway(policy)
    resource = {"workspace": "isolated", "plan_source_hash": "c" * 64}

    with pytest.raises(ApprovalRequiredError) as initial:
        gateway.authorize("workspace.execute", resource, run_id=run_id)
    original = initial.value.approval
    original.expires_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    ) - timedelta(seconds=1)
    db.commit()

    with pytest.raises(ApprovalRequiredError) as expired:
        gateway.authorize("workspace.execute", resource, run_id=run_id)

    db.refresh(original)
    assert original.status == "EXPIRED"
    assert expired.value.approval.id != original.id
    assert expired.value.approval.status == "PENDING"


def test_opa_failure_is_denied_by_default(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise __import__("httpx").ConnectError("unavailable")

    monkeypatch.setenv("SACM_OPA_URL", "http://opa.invalid")
    monkeypatch.delenv("SACM_OPA_FAIL_CLOSED", raising=False)
    monkeypatch.setattr("sacm.core.policy_service.httpx.post", unavailable)

    decision = PolicyService().evaluate("workspace.execute", {"image": "python"})

    assert decision.allowed is False
    assert "failed" in decision.reason


def test_opa_uses_the_configured_decision_endpoint(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {"result": {"allow": True, "reason": "allowed"}}

    def evaluate(url, **_kwargs):
        captured["url"] = url
        return Response()

    monkeypatch.setenv("SACM_OPA_URL", "http://opa.example")
    monkeypatch.setattr("sacm.core.policy_service.httpx.post", evaluate)

    decision = PolicyService().evaluate("workspace.execute", {"image": "node"})

    assert decision.allowed is True
    assert captured["url"] == "http://opa.example/v1/data/sacm/decision"
