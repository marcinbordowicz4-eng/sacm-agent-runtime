from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import ContextEvent, Run


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
        return self._summarize(task_id, events)

    def summarize_run(self, run_id: str) -> dict[str, Any]:
        run = self.db.get(Run, run_id)
        if run is None:
            raise ValueError("Run not found.")
        events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == run.task_id,
                ContextEvent.event_type == "agent_result",
            )
            .order_by(ContextEvent.created_at, ContextEvent.id)
            .all()
        )
        task_run_count = (
            self.db.query(Run).filter(Run.task_id == run.task_id).count()
        )
        return self._summarize(
            run.task_id,
            [
                event
                for event in events
                if self._event_run_id(event.payload) == run.id
                or (
                    self._event_run_id(event.payload) is None
                    and task_run_count == 1
                )
            ],
        )

    @staticmethod
    def _event_run_id(payload: dict[str, Any]) -> str | None:
        for value in (
            payload.get("run_id"),
            CostService._mapping(payload.get("agent_task_contract")).get("run_id"),
            CostService._mapping(payload.get("agent_result_contract")).get("run_id"),
        ):
            if isinstance(value, str):
                return value
        return None

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _summarize(
        task_id: str, events: list[ContextEvent]
    ) -> dict[str, Any]:
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
                if not isinstance(record, dict):
                    continue
                provider = record.get("provider")
                model = record.get("model")
                if not isinstance(provider, str) or not isinstance(model, str):
                    continue
                total = totals[(provider, model)]
                input_tokens = record.get("input_tokens", 0)
                output_tokens = record.get("output_tokens", 0)
                if isinstance(input_tokens, int) and not isinstance(input_tokens, bool):
                    total["input_tokens"] += input_tokens
                if isinstance(output_tokens, int) and not isinstance(output_tokens, bool):
                    total["output_tokens"] += output_tokens
                cost = record.get("estimated_cost_usd")
                if isinstance(cost, (int, float)) and not isinstance(cost, bool):
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
            if isinstance(record, dict)
            and isinstance(record.get("duration_ms"), int)
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
