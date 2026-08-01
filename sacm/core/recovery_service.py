import os
import re
from typing import Any, Literal

from sqlalchemy.orm import Session

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

_PATTERNS = (
    (
        FailureClassification.COMPILATION,
        r"\b(compile|compiler|syntaxerror|type error|typeerror|build failed)\b",
    ),
    (
        FailureClassification.TEST_REGRESSION,
        r"\b(test(s)? failed|assertionerror|regression|pytest|jest|junit)\b",
    ),
    (
        FailureClassification.MISSING_CONTEXT,
        r"\b(missing context|not enough context|unknown symbol|cannot find definition)\b",
    ),
    (
        FailureClassification.ARCHITECTURE_MISMATCH,
        r"\b(architecture|architectural|wrong layer|boundary violation)\b",
    ),
    (
        FailureClassification.BAD_PLAN,
        r"\b(bad plan|replan|plan is wrong|invalid plan)\b",
    ),
    (
        FailureClassification.API_INCOMPATIBILITY,
        r"\b(api incompat|breaking change|contract mismatch|signature mismatch)\b",
    ),
    (
        FailureClassification.WRONG_ASSUMPTION,
        r"\b(wrong assumption|incorrect assumption|requirement misunderstood)\b",
    ),
    (
        FailureClassification.ENVIRONMENT,
        r"\b(environment|permission denied|disk full|out of memory|connection refused)\b",
    ),
    (
        FailureClassification.MODEL_STUCK,
        r"\b(model stuck|repeated patch|no progress|loop detected|context window)\b",
    ),
)


class RecoveryService:
    """Classifies execution failures and schedules bounded, evidence-backed recovery."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.runs = RunService(db)

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
        return recovered, report, decision

    def classify(self, failure: FailureInputV1 | dict[str, Any]) -> FailureReportV1:
        raw = (
            failure.model_dump(mode="json")
            if isinstance(failure, FailureInputV1)
            else dict(failure)
        )
        message = str(
            raw.get("message") or raw.get("detail") or "Agent execution failed."
        )
        failure_type = str(raw.get("type") or "AgentFailure")
        explicit = raw.get("classification")
        if explicit:
            classification = FailureClassification(str(explicit))
            confidence = float(raw.get("confidence") or 1.0)
        else:
            searchable = f"{failure_type} {message}".lower()
            classification = FailureClassification.TOOL_FAILURE
            confidence = 0.55
            for candidate, pattern in _PATTERNS:
                if re.search(pattern, searchable, re.IGNORECASE):
                    classification = candidate
                    confidence = 0.85
                    break
        retryable = raw.get("retryable")
        if retryable is None:
            retryable = True
        known = {
            "schema_version",
            "classification",
            "type",
            "message",
            "evidence",
            "details",
            "retryable",
            "confidence",
        }
        details = dict(raw.get("details") or {})
        details.update({key: value for key, value in raw.items() if key not in known})
        return FailureReportV1(
            classification=classification,
            type=failure_type,
            message=message,
            evidence=list(raw.get("evidence") or []),
            details=details,
            retryable=bool(retryable),
            confidence=confidence,
        )

    def decide(
        self,
        run: Run,
        failure: FailureReportV1,
        *,
        requested_action: RecoveryAction | None = None,
    ) -> RecoveryDecisionV1:
        max_attempts = int(os.getenv("SACM_MAX_RECOVERY_ATTEMPTS", "3"))
        if max_attempts < 0:
            raise ValueError("SACM_MAX_RECOVERY_ATTEMPTS cannot be negative.")
        attempt = run.recovery_attempt_count + 1
        action = requested_action or _ACTION_BY_FAILURE[failure.classification]
        if attempt > max_attempts or not failure.retryable:
            action = RecoveryAction.ESCALATE
        target = _TARGET_BY_ACTION[action]
        status: Literal["SCHEDULED", "ESCALATED"] = (
            "ESCALATED" if action == RecoveryAction.ESCALATE else "SCHEDULED"
        )
        instructions = self._instructions(action, failure)
        reason = (
            f"Recovery attempt budget exhausted ({max_attempts})."
            if attempt > max_attempts
            else (
                "Failure requires human clarification or plan correction."
                if not failure.retryable
                else f"{failure.classification.value} maps to {action.value}."
            )
        )
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
