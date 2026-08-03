from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import LifecycleMetric, Run


class LifecycleMetricService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        metric: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        value: float = 1.0,
        details: dict[str, Any] | None = None,
    ) -> LifecycleMetric:
        if run_id and task_id is None:
            run = self.db.get(Run, run_id)
            task_id = run.task_id if run else None
        row = LifecycleMetric(
            run_id=run_id,
            task_id=task_id,
            metric=metric,
            value=value,
            details=details or {},
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def summary(self, run_id: str) -> dict[str, Any]:
        rows = (
            self.db.query(
                LifecycleMetric.metric,
                func.count(LifecycleMetric.id),
                func.sum(LifecycleMetric.value),
                func.avg(LifecycleMetric.value),
                func.max(LifecycleMetric.value),
            )
            .filter(LifecycleMetric.run_id == run_id)
            .group_by(LifecycleMetric.metric)
            .order_by(LifecycleMetric.metric)
            .all()
        )
        return {
            "schema_version": "lifecycle-metrics/v1",
            "run_id": run_id,
            "metrics": [
                {
                    "metric": metric,
                    "count": count,
                    "sum": float(total or 0),
                    "average": float(average or 0),
                    "maximum": float(maximum or 0),
                }
                for metric, count, total, average, maximum in rows
            ],
            # Agent-result events are durable task telemetry. Summarizing them here
            # also exposes usage from missions created before lifecycle rows existed.
            "telemetry": self._agent_telemetry(run_id),
        }

    def _agent_telemetry(self, run_id: str) -> dict[str, Any] | None:
        from sacm.core.cost_service import CostService

        try:
            return CostService(self.db).summarize_run(run_id)
        except ValueError:
            return None
