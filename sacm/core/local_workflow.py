from typing import Any

from sqlalchemy.orm import Session

from sacm.core.evidence_service import EvidenceService
from sacm.core.orchestrator import Orchestrator
from sacm.core.recovery_service import RecoveryService
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import EvidencePack
from sacm.schemas.recovery import RecoveryAction


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
        elif run.status not in {"PLANNING", "FIXING", "IMPLEMENTING"}:
            raise ValueError(f"Run {run_id} cannot execute from {run.status}")

        step = self.runs.add_step(
            run_id,
            name="legacy-orchestrator",
            input_={"task_id": run.task_id},
            idempotency_key=f"{run_id}:legacy-orchestrator",
        )
        while True:
            if step.status == "FAILED":
                failure = (step.output or {}).get("failure") or {
                    "type": "PreviousExecutionFailure",
                    "message": "Retrying a previously failed workflow step.",
                }
                step, _, decision = RecoveryService(self.db).handle(
                    run_id, step.id, failure
                )
                if decision.status == "ESCALATED":
                    return {
                        "run_id": run_id,
                        "status": "FAILED",
                        "error": failure["message"],
                        "recovery": decision.model_dump(mode="json"),
                    }
            self.runs.start_step(run_id, step.id)
            current = self.runs.get(run_id)
            if current is None:
                raise ValueError(f"Run {run_id} disappeared during execution")
            if current.status != "IMPLEMENTING":
                self.runs.transition(
                    run_id,
                    "IMPLEMENTING",
                    "WorkflowImplementing",
                    step_id=step.id,
                )
            self.db.expire_all()
            execution_run = self.runs.get(run_id)
            if execution_run is None:
                raise ValueError(f"Run {run_id} disappeared during execution")
            expected_run_status = execution_run.status
            expected_run_updated_at = execution_run.updated_at
            try:
                output = Orchestrator(self.db).run_task(
                    run.task_id,
                    run_id=run_id,
                    **({"max_steps": max_steps} if max_steps else {}),
                    recovery_context=self._recovery_context(step),
                )
                self.db.expire_all()
                current_run = self.runs.get(run_id)
                if current_run is None:
                    raise ValueError(f"Run {run_id} disappeared during execution")
                if (
                    current_run.cancellation_requested
                    or current_run.status == "CANCELLED"
                ):
                    self.runs.interrupt_step(
                        run_id,
                        step.id,
                        reason="Run was cancelled while the orchestrator was executing.",
                        cancelled=True,
                    )
                    return {"run_id": run_id, "status": current_run.status}
                if (
                    current_run.status != expected_run_status
                    or current_run.updated_at != expected_run_updated_at
                ):
                    self.runs.interrupt_step(
                        run_id,
                        step.id,
                        reason="The run changed while the orchestrator was executing.",
                    )
                    return {
                        "run_id": run_id,
                        "status": current_run.status,
                        "output": output,
                        "stale_result_ignored": True,
                    }
                if output["status"] != "done":
                    recovery = output.get("recovery") or {}
                    decision_payload = recovery.get("decision") or {}
                    if decision_payload.get("status") == "ESCALATED":
                        failure = recovery.get("failure") or {
                            "type": "TaskRecoveryEscalated",
                            "message": "Task-level autonomous recovery escalated.",
                            "retryable": False,
                        }
                        self.runs.fail_step(run_id, step.id, failure)
                        step, _, decision = RecoveryService(self.db).handle(
                            run_id,
                            step.id,
                            failure,
                            requested_action=RecoveryAction.ESCALATE,
                        )
                        return {
                            "run_id": run_id,
                            "status": "FAILED",
                            "error": failure["message"],
                            "recovery": decision.model_dump(mode="json"),
                        }
                    raise RuntimeError(
                        "The orchestrator did not produce a verified completed task."
                    )
                self.runs.complete_step(run_id, step.id, output)
                self.runs.transition(
                    run_id, "REVIEWING", "WorkflowReviewing", step_id=step.id
                )
                self.runs.transition(
                    run_id, "TESTING", "WorkflowTesting", step_id=step.id
                )
                self.runs.transition(
                    run_id, "DELIVERING", "WorkflowDelivering", step_id=step.id
                )
                evidence_service = EvidenceService(self.db)
                evidence_pack = (
                    self.db.query(EvidencePack)
                    .filter(EvidencePack.run_id == run_id)
                    .order_by(EvidencePack.created_at.desc(), EvidencePack.id.desc())
                    .first()
                )
                if evidence_pack is None:
                    evidence_pack = evidence_service.build(
                        run_id,
                        trusted_internal=True,
                    )
                evidence_verification = evidence_service.verify(
                    run_id,
                    evidence_pack.id,
                    trusted_internal=True,
                )
                if evidence_verification.status == "INVALID":
                    raise RuntimeError(
                        "Evidence Pack integrity verification failed: "
                        + "; ".join(evidence_verification.errors)
                    )
                completed = self.runs.transition(
                    run_id,
                    "COMPLETED",
                    "RunCompleted",
                    step_id=step.id,
                )
                final_pack = evidence_service.build(
                    run_id,
                    trusted_internal=True,
                )
                final_verification = evidence_service.verify(
                    run_id,
                    final_pack.id,
                    trusted_internal=True,
                )
                if final_verification.status == "INVALID":
                    raise RuntimeError(
                        "Final Evidence Pack integrity verification failed: "
                        + "; ".join(final_verification.errors)
                    )
                analytics_error = self._refresh_analytics(run_id)
                if analytics_error:
                    return {
                        "run_id": run_id,
                        "status": completed.status,
                        "output": output,
                        "analytics": {"status": "FAILED", "error": analytics_error},
                    }
                return {
                    "run_id": run_id,
                    "status": completed.status,
                    "output": output,
                    "analytics": {"status": "COMPLETED"},
                }
            except Exception as exc:
                self.db.expire_all()
                current_run = self.runs.get(run_id)
                if current_run is None:
                    raise ValueError(
                        f"Run {run_id} disappeared during execution"
                    ) from exc
                if (
                    current_run.cancellation_requested
                    or current_run.status == "CANCELLED"
                ):
                    self.runs.interrupt_step(
                        run_id,
                        step.id,
                        reason="Run was cancelled while the orchestrator was executing.",
                        cancelled=True,
                    )
                    return {
                        "run_id": run_id,
                        "status": current_run.status,
                        "error": str(exc),
                        "stale_result_ignored": True,
                    }
                if (
                    current_run.status != expected_run_status
                    or current_run.updated_at != expected_run_updated_at
                ):
                    self.runs.interrupt_step(
                        run_id,
                        step.id,
                        reason="The run changed while the orchestrator was executing.",
                    )
                    return {
                        "run_id": run_id,
                        "status": current_run.status,
                        "error": str(exc),
                        "stale_result_ignored": True,
                    }
                failure = {"type": exc.__class__.__name__, "message": str(exc)}
                self.runs.fail_step(run_id, step.id, failure)
                step, _, decision = RecoveryService(self.db).handle(
                    run_id, step.id, failure
                )
                if decision.status == "ESCALATED":
                    return {
                        "run_id": run_id,
                        "status": "FAILED",
                        "error": str(exc),
                        "recovery": decision.model_dump(mode="json"),
                    }

    def _refresh_analytics(self, run_id: str) -> str | None:
        from sacm.core.analytics_service import AnalyticsService

        try:
            AnalyticsService(self.db).recompute_run(run_id)
        except Exception as exc:
            self.db.rollback()
            return str(exc)
        return None

    @staticmethod
    def _recovery_context(step) -> dict[str, Any] | None:
        task = step.input_.get("agent_task") or {}
        return (
            (task.get("execution_context") or {}).get("recovery")
            or step.input_.get("recovery")
            or (step.output or {}).get("recovery")
        )
