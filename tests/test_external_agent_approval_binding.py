import pytest

from sacm.core.external_agent_service import ExternalAgentService
from sacm.core.policy_service import PolicyService
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import Approval
from sacm.schemas.contracts import AgentResultV1, ExternalAgentStepCreate
from sacm.schemas.run import RunCreate


def test_plan_change_supersedes_approved_agent_result_and_rebinds_step(
    db, monkeypatch
):
    run = RunService(db).create(
        RunCreate(title="Plan binding", description="Approval binding coverage.")
    )
    service = ExternalAgentService(db)
    scheduled = service.schedule(
        run.id,
        ExternalAgentStepCreate(
            framework="codex",
            agent_name="delivery",
            idempotency_key="codex:delivery:plan-binding",
            role="coder",
            objective="Prepare delivery.",
            token_budget=500,
            timeout_seconds=120,
        ),
    )
    result = AgentResultV1(
        run_id=run.id,
        step_id=scheduled.step.id,
        status="NEEDS_APPROVAL",
        summary="Ready to push the branch.",
        actions=[{"action": "git.push"}],
    )
    waiting = service.submit(run.id, scheduled.step.id, result)
    assert waiting.approval_id
    PolicyService(db).decide(
        waiting.approval_id, True, "maintainer", "Approved original plan."
    )

    new_binding = {
        "execution_plan_id": "new-plan",
        "plan_revision": 2,
        "plan_source_hash": "b" * 64,
    }
    monkeypatch.setattr(service, "_plan_binding", lambda _run_id: new_binding)
    with pytest.raises(ValueError, match="superseded execution plan"):
        service.submit(
            run.id,
            scheduled.step.id,
            result.model_copy(update={"status": "COMPLETED"}),
        )

    old_approval = db.get(Approval, waiting.approval_id)
    assert old_approval is not None
    assert old_approval.status == "SUPERSEDED"

    rebound = service.submit(run.id, scheduled.step.id, result)
    assert rebound.step.status == "AWAITING_APPROVAL"
    assert rebound.approval_id != waiting.approval_id
    assert rebound.step.output["sacm_approval_id"] == rebound.approval_id
