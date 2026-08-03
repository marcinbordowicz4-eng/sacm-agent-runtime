from types import SimpleNamespace

from sacm.core.cost_service import CostService
from sacm.core.event_service import EventService
from sacm.core.lifecycle_metric_service import LifecycleMetricService
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import Task
from sacm.schemas.contracts import AgentTaskV1
from sacm.schemas.run import RunCreate


def test_cost_service_aggregates_provider_reported_usage(db):
    task = Task(title="Cost task", description="Track cost")
    db.add(task)
    db.commit()
    result = SimpleNamespace(
        summary="done",
        confidence=1.0,
        next_state_hint="done",
        actions=[],
        artifacts=[
            {
                "type": "usage",
                "provider": "codex",
                "model": "gpt-5-codex",
                "operation": "code_execution",
                "input_tokens": 100,
                "output_tokens": 20,
                "estimated_cost_usd": 0.012,
            },
            {"type": "tool_execution", "duration_ms": 40, "returncode": 0},
            {"type": "tool_execution", "duration_ms": 20, "returncode": 1},
        ],
    )
    EventService(db).save_agent_result(task.id, "CodexExecutor", result)

    summary = CostService(db).summarize_task(task.id)

    assert summary["input_tokens"] == 100
    assert summary["output_tokens"] == 20
    assert summary["estimated_cost_usd"] == 0.012
    assert summary["usage"][0]["model"] == "gpt-5-codex"
    assert summary["tool_execution_count"] == 2
    assert summary["tool_duration_ms"] == 60
    assert summary["failed_tool_execution_count"] == 1


def test_cost_service_ignores_non_usage_artifacts(db):
    task = Task(title="Cost task", description="Track cost")
    db.add(task)
    db.commit()
    result = SimpleNamespace(
        summary="done",
        confidence=1.0,
        next_state_hint="done",
        actions=[],
        artifacts=[{"type": "tool_execution", "duration_ms": 20, "returncode": 0}],
    )
    EventService(db).save_agent_result(task.id, "CodexExecutor", result)

    summary = CostService(db).summarize_task(task.id)

    assert summary["usage"] == []
    assert summary["estimated_cost_usd"] == 0
    assert summary["tool_execution_count"] == 1


def test_lifecycle_summary_derives_telemetry_for_existing_run_events(db):
    run = RunService(db).create(
        RunCreate(title="Telemetry run", description="Preserve mission telemetry")
    )
    EventService(db).save(
        run.task_id,
        "agent_result",
        {
            "run_id": run.id,
            "usage": [
                {
                    "provider": "copilot",
                    "model": "gpt-5",
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "estimated_cost_usd": 0.015,
                }
            ],
            "tool_execution": [
                {"duration_ms": 80, "returncode": 0},
                {"duration_ms": 20, "returncode": 1},
            ],
        },
    )

    telemetry = LifecycleMetricService(db).summary(run.id)["telemetry"]

    assert telemetry["input_tokens"] == 120
    assert telemetry["output_tokens"] == 30
    assert telemetry["estimated_cost_usd"] == 0.015
    assert telemetry["tool_execution_count"] == 2
    assert telemetry["failed_tool_execution_count"] == 1


def test_live_telemetry_is_run_scoped_and_does_not_double_count_final_result(db):
    run = RunService(db).create(
        RunCreate(title="Live telemetry", description="Track streamed usage")
    )
    task_contract = AgentTaskV1(
        run_id=run.id,
        step_id="agent-1",
        role="coder",
        objective="Implement telemetry.",
        token_budget=100,
        timeout_seconds=60,
    )
    EventService(db).save_agent_telemetry(
        run.task_id,
        run_id=run.id,
        step_id=task_contract.step_id,
        event={
            "type": "provider_usage",
            "usage": {
                "provider": "copilot",
                "model": "gpt-5",
                "input_tokens": 11,
                "output_tokens": 7,
            },
        },
    )
    EventService(db).save_agent_telemetry(
        run.task_id,
        run_id=run.id,
        step_id=task_contract.step_id,
        event={
            "type": "tool_completed",
            "tool": "copilot",
            "duration_ms": 42,
            "returncode": 0,
        },
    )
    duplicate_result = SimpleNamespace(
        summary="done",
        confidence=1.0,
        next_state_hint="testing",
        actions=[],
        artifacts=[
            {
                "type": "usage",
                "provider": "copilot",
                "model": "gpt-5",
                "input_tokens": 11,
                "output_tokens": 7,
            },
            {
                "type": "tool_execution",
                "tool": "copilot",
                "duration_ms": 42,
                "returncode": 0,
            },
        ],
    )
    EventService(db).save_agent_result(
        run.task_id,
        "CodexExecutor",
        duplicate_result,
        task_contract=task_contract,
        telemetry_scope=f"{run.id}:{task_contract.step_id}",
    )

    summary = LifecycleMetricService(db).summary(run.id)["telemetry"]

    assert summary["input_tokens"] == 11
    assert summary["output_tokens"] == 7
    assert summary["tool_execution_count"] == 1
