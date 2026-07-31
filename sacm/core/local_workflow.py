from typing import Any

from sqlalchemy.orm import Session

from sacm.core.evidence_service import EvidenceService
from sacm.core.orchestrator import Orchestrator
from sacm.core.run_service import RunService


class LocalWorkflow:
    """A persistent local workflow backend for the existing orchestrator."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.runs = RunService(db)

    def execute(self, run_id: str, max_steps: int | None = None) -> dict[str, Any]:
        run = self.runs.get(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        if run.cancellation_requested or run.status == "CANCELLED":
            raise ValueError(f"Run {run_id} is cancelled")
        if run.status == "CREATED":
            self.runs.transition(run_id, "PLANNING", "RunStarted")
        elif run.status == "FAILED":
            self.runs.resume(run_id)
        elif run.status != "PLANNING":
            raise ValueError(f"Run {run_id} cannot execute from {run.status}")

        step = self.runs.add_step(
            run_id,
            name="legacy-orchestrator",
            input_={"task_id": run.task_id},
            idempotency_key=f"{run_id}:legacy-orchestrator",
        )
        if step.status == "FAILED":
            step = self.runs.retry_step(run_id, step.id)
        self.runs.start_step(run_id, step.id)
        self.runs.transition(
            run_id,
            "IMPLEMENTING",
            "WorkflowImplementing",
            step_id=step.id,
        )
        try:
            output = Orchestrator(self.db).run_task(
                run.task_id,
                run_id=run_id,
                **({"max_steps": max_steps} if max_steps else {}),
            )
            current_run = self.runs.get(run_id)
            if current_run is None:
                raise ValueError(f"Run {run_id} disappeared during execution")
            if current_run.cancellation_requested:
                return {"run_id": run_id, "status": self.runs.cancel(run_id).status}
            if output["status"] != "done":
                raise RuntimeError(
                    "The orchestrator did not produce a verified completed task."
                )
            self.runs.complete_step(run_id, step.id, output)
            self.runs.transition(run_id, "REVIEWING", "WorkflowReviewing", step_id=step.id)
            self.runs.transition(run_id, "TESTING", "WorkflowTesting", step_id=step.id)
            self.runs.transition(run_id, "DELIVERING", "WorkflowDelivering", step_id=step.id)
            EvidenceService(self.db).build(run_id, trusted_internal=True)
            completed = self.runs.transition(
                run_id,
                "COMPLETED",
                "RunCompleted",
                step_id=step.id,
            )
            EvidenceService(self.db).build(run_id, trusted_internal=True)
            return {"run_id": run_id, "status": completed.status, "output": output}
        except Exception as exc:
            self.runs.fail_step(
                run_id,
                step.id,
                {"type": exc.__class__.__name__, "message": str(exc)},
            )
            failed = self.runs.transition(
                run_id,
                "FAILED",
                "RunFailed",
                payload={"type": exc.__class__.__name__, "message": str(exc)},
                step_id=step.id,
            )
            return {"run_id": run_id, "status": failed.status, "error": str(exc)}
