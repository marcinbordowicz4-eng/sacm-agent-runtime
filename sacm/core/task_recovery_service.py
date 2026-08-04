import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.recovery_service import RecoveryService
from sacm.schemas.contracts import AgentResultV1
from sacm.schemas.recovery import (
    FailureClassification,
    FailureReportV1,
    RecoveryDecisionV1,
)
from sacm.schemas.result import AgentResult
from sacm.schemas.verification import VerificationMatrixV2


class TaskRecoveryService:
    """Diagnose incomplete legacy task steps and plan bounded recovery."""

    def __init__(self, db: Session) -> None:
        self.recovery = RecoveryService(db)

    @staticmethod
    def outcome_signature(
        agent_name: str,
        result: AgentResult,
        verification: VerificationMatrixV2,
    ) -> str:
        del agent_name
        payload = {
            "patches": [
                artifact.get("content")
                for artifact in result.artifacts
                if artifact.get("type") == "diff"
                and isinstance(artifact.get("content"), str)
            ],
            "blocking_reasons": verification.blocking_reasons,
            "verification": {
                "build_status": verification.build_status,
                "regression_status": verification.regression.status,
                "contract_status": verification.contract_compatibility.status,
                "security_status": verification.security_status,
                "test_integrity_status": verification.test_integrity.status,
            },
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def diagnose(
        self,
        *,
        agent_result: AgentResultV1,
        result: AgentResult,
        verification: VerificationMatrixV2,
        repeated_outcome: bool,
        step_budget_exhausted: bool,
    ) -> FailureReportV1 | None:
        raw_failure: dict[str, Any] | None = None
        if agent_result.failure is not None:
            supplied = agent_result.failure or {
                "type": "AgentExecutionFailure",
                "message": result.summary or "Agent execution failed.",
            }
            raw_failure = (
                supplied.model_dump(mode="json")
                if hasattr(supplied, "model_dump")
                else dict(supplied)
            )
            serialized_failure = json.dumps(raw_failure, default=str)
            if (
                raw_failure.get("classification")
                == FailureClassification.ENVIRONMENT
                or "INFRASTRUCTURE_RESOURCE" in serialized_failure
            ):
                return self.recovery.classify(raw_failure)

        explicit = self._verification_failure(verification)
        if explicit is not None:
            return self.recovery.classify(explicit)

        if result.next_state_hint == "blocked":
            return self.recovery.classify(
                {
                    "classification": FailureClassification.MISSING_CONTEXT,
                    "type": "AgentBlocked",
                    "message": result.summary or "Agent requested missing context.",
                    "confidence": 0.9,
                    "details": {
                        "blocking_reasons": verification.blocking_reasons,
                    },
                }
            )

        if raw_failure is not None or agent_result.status == "FAILED":
            return self.recovery.classify(
                raw_failure
                or {
                    "type": "AgentExecutionFailure",
                    "message": result.summary or "Agent execution failed.",
                }
            )

        if repeated_outcome:
            return self.recovery.classify(
                {
                    "classification": FailureClassification.MODEL_STUCK,
                    "type": "NoProgress",
                    "message": (
                        "The same agent outcome and verification blockers repeated "
                        "without measurable progress."
                    ),
                    "confidence": 1.0,
                    "details": {
                        "agent_name": result.agent_name,
                        "blocking_reasons": verification.blocking_reasons,
                    },
                }
            )

        if step_budget_exhausted:
            return self.recovery.classify(
                {
                    "classification": FailureClassification.MODEL_STUCK,
                    "type": "StepBudgetExhausted",
                    "message": (
                        "The task exhausted its agent step budget without verified "
                        "completion."
                    ),
                    "confidence": 1.0,
                    "retryable": False,
                    "details": {
                        "agent_name": result.agent_name,
                        "blocking_reasons": verification.blocking_reasons,
                    },
                }
            )
        return None

    def plan(
        self,
        failure: FailureReportV1,
        *,
        attempt_count: int,
        history: list[dict[str, Any]],
    ) -> RecoveryDecisionV1:
        return self.recovery.decide_for_attempt(
            failure,
            attempt_count=attempt_count,
            history=history,
        )

    @staticmethod
    def _verification_failure(
        verification: VerificationMatrixV2,
    ) -> dict[str, Any] | None:
        details = {
            "blocking_reasons": verification.blocking_reasons,
            "build_status": verification.build_status,
            "regression_status": verification.regression.status,
            "contract_status": verification.contract_compatibility.status,
            "security_status": verification.security_status,
            "test_integrity_status": verification.test_integrity.status,
        }
        if verification.build_status == "FAIL":
            classification = FailureClassification.COMPILATION
            message = "Build verification failed."
        elif verification.regression.status == "FAIL":
            classification = FailureClassification.TEST_REGRESSION
            message = "Regression verification failed."
        elif verification.contract_compatibility.status == "FAIL":
            classification = FailureClassification.API_INCOMPATIBILITY
            message = "API or schema compatibility verification failed."
        elif any(requirement.status == "FAIL" for requirement in verification.requirements):
            classification = FailureClassification.WRONG_ASSUMPTION
            message = "At least one mandatory requirement verification failed."
        elif verification.test_integrity.status == "FAIL":
            classification = FailureClassification.TEST_REGRESSION
            message = "Test integrity verification failed."
        elif verification.security_status == "FAIL":
            classification = FailureClassification.WRONG_ASSUMPTION
            message = "Security verification failed and requires a revised plan."
        else:
            return None
        return {
            "classification": classification,
            "type": "VerificationFailure",
            "message": message,
            "confidence": 1.0,
            "details": details,
        }
