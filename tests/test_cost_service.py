from types import SimpleNamespace

from sacm.core.cost_service import CostService
from sacm.core.event_service import EventService
from sacm.infrastructure.db.models import Task


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
