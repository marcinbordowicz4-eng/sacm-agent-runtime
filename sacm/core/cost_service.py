from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import ContextEvent


class CostService:
    """Summarizes provider-reported model usage persisted with task events."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def summarize_task(self, task_id: str) -> dict[str, Any]:
        events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == task_id,
                ContextEvent.event_type == "agent_result",
            )
            .all()
        )
        totals: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "cost_estimation_available": False,
            }
        )
        for event in events:
            for record in event.payload.get("usage", []):
                provider = record.get("provider")
                model = record.get("model")
                if not isinstance(provider, str) or not isinstance(model, str):
                    continue
                total = totals[(provider, model)]
                total["input_tokens"] += int(record.get("input_tokens", 0))
                total["output_tokens"] += int(record.get("output_tokens", 0))
                cost = record.get("estimated_cost_usd")
                if isinstance(cost, (int, float)):
                    total["estimated_cost_usd"] += cost
                    total["cost_estimation_available"] = True

        entries = [
            {"provider": provider, "model": model, **values}
            for (provider, model), values in sorted(totals.items())
        ]
        tool_executions = [
            record
            for event in events
            for record in event.payload.get("tool_execution", [])
            if isinstance(record.get("duration_ms"), int)
            and isinstance(record.get("returncode"), int)
        ]
        return {
            "task_id": task_id,
            "usage": entries,
            "input_tokens": sum(item["input_tokens"] for item in entries),
            "output_tokens": sum(item["output_tokens"] for item in entries),
            "estimated_cost_usd": sum(item["estimated_cost_usd"] for item in entries),
            "cost_estimation_available": any(
                item["cost_estimation_available"] for item in entries
            ),
            "tool_execution_count": len(tool_executions),
            "tool_duration_ms": sum(record["duration_ms"] for record in tool_executions),
            "failed_tool_execution_count": sum(
                record["returncode"] != 0 for record in tool_executions
            ),
        }
