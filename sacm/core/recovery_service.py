import os
from typing import Any, Literal

from sqlalchemy.orm import Session

from sacm.core.diagnostic_service import DiagnosticService
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import Run, RunStep
from sacm.schemas.recovery import (
    FailureClassification,
    FailureInputV1,
    FailureReportV1,
    RecoveryAction,
    RecoveryDecisionV1,
)

_ACTION_BY_FAILURE = {
    FailureClassification.COMPILATION: RecoveryAction.REPAIR_CODE,
    FailureClassification.TEST_REGRESSION: RecoveryAction.DEBUG,
    FailureClassification.WRONG_ASSUMPTION: RecoveryAction.REPLAN,
    FailureClassification.MISSING_CONTEXT: RecoveryAction.EXPAND_CONTEXT,
    FailureClassification.ARCHITECTURE_MISMATCH: RecoveryAction.REPLAN,
    FailureClassification.BAD_PLAN: RecoveryAction.REPLAN,
    FailureClassification.API_INCOMPATIBILITY: RecoveryAction.REPAIR_CODE,
    FailureClassification.ENVIRONMENT: RecoveryAction.RETRY,
    FailureClassification.TOOL_FAILURE: RecoveryAction.RETRY,
    FailureClassification.MODEL_STUCK: RecoveryAction.SWITCH_MODEL,
}

_TARGET_BY_ACTION = {
    RecoveryAction.REPAIR_CODE: "FIXING",
    RecoveryAction.DEBUG: "FIXING",
    RecoveryAction.REPLAN: "PLANNING",
    RecoveryAction.EXPAND_CONTEXT: "PLANNING",
    RecoveryAction.SWITCH_MODEL: "PLANNING",
    RecoveryAction.RETRY: "IMPLEMENTING",
    RecoveryAction.ESCALATE: "FAILED",
}

class RecoveryService:
    """Classifies execution failures and schedules bounded, evidence-backed recovery."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.runs = RunService(db)
        self.diagnostics = DiagnosticService()

    def handle(
        self,
        run_id: str,
        step_id: str,
        failure: FailureInputV1 | dict[str, Any],
        *,
        requested_action: RecoveryAction | None = None,
        commit: bool = True,
    ) -> tuple[RunStep, FailureReportV1, RecoveryDecisionV1]:
        run = self._require_run(run_id)
        step = self.runs.get_step(run_id, step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found for run {run_id}.")
        if step.status != "FAILED":
            raise ValueError("Recovery requires a failed step.")
        report = self.classify(failure)
        decision = self.decide(run, report, requested_action=requested_action)
        recovered = self.runs.schedule_recovery(
            run_id,
            step_id,
            report.model_dump(mode="json"),
            decision.model_dump(mode="json"),
            commit=commit,
        )
        if commit:
            from sacm.core.lifecycle_metric_service import LifecycleMetricService

            LifecycleMetricService(self.db).record(
                "workflow.recovery_decision",
                run_id=run_id,
                details={
                    "step_id": step_id,
                    "classification": report.classification.value,
                    "action": decision.action.value,
                    "status": decision.status,
                    "attempt": decision.attempt,
                },
            )
        return recovered, report, decision

    def classify(self, failure: FailureInputV1 | dict[str, Any]) -> FailureReportV1:
        raw = (
            failure.model_dump(mode="json")
            if isinstance(failure, FailureInputV1)
            else dict(failure)
        )
        return self.diagnostics.diagnose(raw)

    def decide(
        self,
        run: Run,
        failure: FailureReportV1,
        *,
        requested_action: RecoveryAction | None = None,
    ) -> RecoveryDecisionV1:
        return self.decide_for_attempt(
            failure,
            attempt_count=run.recovery_attempt_count,
            history=list((run.recovery_state or {}).get("history", [])),
            requested_action=requested_action,
        )

    def decide_for_attempt(
        self,
        failure: FailureReportV1,
        *,
        attempt_count: int,
        history: list[dict[str, Any]] | None = None,
        requested_action: RecoveryAction | None = None,
    ) -> RecoveryDecisionV1:
        max_attempts = int(os.getenv("SACM_MAX_RECOVERY_ATTEMPTS", "3"))
        minimum_confidence = float(
            os.getenv("SACM_DIAGNOSTIC_MIN_CONFIDENCE", "0.6")
        )
        if max_attempts < 0:
            raise ValueError("SACM_MAX_RECOVERY_ATTEMPTS cannot be negative.")
        if not 0 <= minimum_confidence <= 1:
            raise ValueError(
                "SACM_DIAGNOSTIC_MIN_CONFIDENCE must be between 0 and 1."
            )
        if attempt_count < 0:
            raise ValueError("Recovery attempt count cannot be negative.")
        attempt = attempt_count + 1
        action = requested_action or _ACTION_BY_FAILURE[failure.classification]
        repeated_identical_patch = self._repeated_identical_patch(
            history or [], failure
        )
        autonomous_confidence_block = (
            requested_action is None and failure.confidence < minimum_confidence
        )
        if (
            attempt > max_attempts
            or not failure.retryable
            or autonomous_confidence_block
            or repeated_identical_patch
        ):
            action = RecoveryAction.ESCALATE
        target = _TARGET_BY_ACTION[action]
        status: Literal["SCHEDULED", "ESCALATED"] = (
            "ESCALATED" if action == RecoveryAction.ESCALATE else "SCHEDULED"
        )
        instructions = self._instructions(action, failure)
        if attempt > max_attempts:
            reason = f"Recovery attempt budget exhausted ({max_attempts})."
        elif not failure.retryable:
            reason = "Failure is explicitly non-retryable."
        elif autonomous_confidence_block:
            reason = (
                f"Diagnostic confidence {failure.confidence:.3f} is below "
                f"the autonomous threshold {minimum_confidence:.3f}."
            )
        elif repeated_identical_patch:
            reason = "The same patch and root cause already failed; retry is blocked."
        else:
            reason = f"{failure.classification.value} maps to {action.value}."
        return RecoveryDecisionV1(
            action=action,
            status=status,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            target_run_status=target,
            instructions=instructions,
        )

    @staticmethod
    def _repeated_identical_patch(
        history: list[dict[str, Any]], failure: FailureReportV1
    ) -> bool:
        patch_hash = failure.details.get("patch_hash")
        fingerprint = failure.diagnosis_fingerprint
        if not patch_hash or not fingerprint:
            return False
        return any(
            item.get("failure", {}).get("diagnosis_fingerprint") == fingerprint
            and item.get("failure", {}).get("details", {}).get("patch_hash")
            == patch_hash
            for item in history
        )

    @staticmethod
    def _instructions(action: RecoveryAction, failure: FailureReportV1) -> list[str]:
        instructions = {
            RecoveryAction.REPAIR_CODE: [
                "Use the diagnostics to produce the smallest compatible code repair.",
                "Add or update a regression test before resubmitting.",
            ],
            RecoveryAction.DEBUG: [
                "Reproduce the failure and isolate its root cause before editing.",
                "Return the reproducer and the passing regression test as evidence.",
            ],
            RecoveryAction.REPLAN: [
                "Discard assumptions invalidated by the failure.",
                "Produce a revised plan linked to the acceptance criteria.",
            ],
            RecoveryAction.EXPAND_CONTEXT: [
                "Request the missing symbols, callers, tests, and contracts.",
                "Do not modify code until the context gap is resolved.",
            ],
            RecoveryAction.SWITCH_MODEL: [
                "Route the retry to a different model family.",
                "Include the previous failed approach as negative evidence.",
            ],
            RecoveryAction.RETRY: [
                "Retry only after validating the execution environment and tools.",
            ],
            RecoveryAction.ESCALATE: [
                "Stop autonomous execution and request human intervention.",
            ],
        }[action]
        return [*instructions, f"Failure: {failure.message}"]

    def _require_run(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found.")
        return run
