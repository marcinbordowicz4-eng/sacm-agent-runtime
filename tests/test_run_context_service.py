from sacm.core.event_service import EventService
from sacm.core.run_context_service import RunContextService
from sacm.core.run_service import RunService
from sacm.core.tenancy_service import TenancyService
from sacm.schemas.contracts import AgentResultV1, AgentTaskV1
from sacm.schemas.result import AgentResult
from sacm.schemas.run import RunCreate


def test_run_context_contains_client_task_and_actual_agent_invocations(db):
    tenancy = TenancyService(db)
    organization = tenancy.create_organization("reestate", "Reestate", "owner")
    project = tenancy.create_project(
        organization.id,
        "mobile",
        "Reestate Mobile",
        "owner",
        "reestate-io/real-estate-chain-mobile",
        "/repositories/reestate",
    )
    run = RunService(db).create(
        RunCreate(
            title="Fix checkout",
            description="Fix checkout validation and run tests.",
            project_id=project.id,
            target_repo_path=project.repository_path,
        )
    )
    task_contract = AgentTaskV1(
        run_id=run.id,
        step_id="step-1",
        role="coder",
        objective="Fix checkout.",
        token_budget=100,
        timeout_seconds=60,
    )
    result_contract = AgentResultV1(
        run_id=run.id,
        step_id="step-1",
        status="COMPLETED",
        summary="Checkout fixed.",
        confidence=0.9,
        next_state_hint="testing",
    )
    EventService(db).save_agent_result(
        run.task_id,
        "CodexExecutor",
        AgentResult(
            agent_name="CodexExecutor",
            summary="Checkout fixed.",
            confidence=0.9,
            next_state_hint="testing",
        ),
        task_contract=task_contract,
        result_contract=result_contract,
    )

    context = RunContextService(db).build(run)

    assert context["organization"]["name"] == "Reestate"
    assert context["project"]["repository_full_name"] == (
        "reestate-io/real-estate-chain-mobile"
    )
    assert context["task"]["description"] == (
        "Fix checkout validation and run tests."
    )
    assert context["agents"][0]["name"] == "CodexExecutor"
    assert context["agents"][0]["role"] == "coder"
    assert context["agents"][0]["status"] == "COMPLETED"


def test_legacy_run_context_does_not_report_runtime_actors_as_agents(db):
    run = RunService(db).create(
        RunCreate(title="Legacy task", description="A run without a project.")
    )
    RunService(db).transition(run.id, "PLANNING", "RunStarted", actor="system")

    context = RunContextService(db).build(run)

    assert context["organization"] is None
    assert context["project"] is None
    assert context["agents"] == []
