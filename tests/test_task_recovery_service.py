from sacm.core.task_recovery_service import TaskRecoveryService
from sacm.schemas.contracts import AgentResultV1
from sacm.schemas.recovery import FailureClassification, RecoveryAction
from sacm.schemas.result import AgentResult
from sacm.schemas.verification import (
    ContractCompatibilityResultV1,
    RegressionProofV1,
    VerificationMatrixV2,
)
from sacm.schemas.verification import TestIntegrityResultV1 as IntegrityResultV1


def _result() -> AgentResult:
    return AgentResult(
        agent_name="SecurityDelivery",
        summary="CodeQL and dependency review workflows are configured.",
        actions=[{"type": "SECURITY_CI_PREFLIGHT"}],
        artifacts=[],
        confidence=1.0,
        next_state_hint="reviewing",
    )


def _contract(*, status="COMPLETED", failure=None) -> AgentResultV1:
    return AgentResultV1(
        run_id="legacy:task-1",
        step_id="agent-1",
        status=status,
        summary="Agent result",
        failure=failure,
    )


def _verification(
    *,
    build_status="MISSING",
    regression_status="MISSING",
    contract_status="MISSING",
) -> VerificationMatrixV2:
    return VerificationMatrixV2(
        task_id="task-1",
        strict=False,
        build_status=build_status,
        regression=RegressionProofV1(
            focused_test_status="MISSING",
            failed_before_fix=False,
            affected_area_status="MISSING",
            status=regression_status,
        ),
        contract_compatibility=ContractCompatibilityResultV1(
            status=contract_status
        ),
        security_status="NOT_APPLICABLE",
        test_integrity=IntegrityResultV1(status="NOT_APPLICABLE"),
        technical_complete=False,
        evidence_complete=False,
        complete=False,
        blocking_reasons=["No successful verification."],
    )


def test_repeated_agent_outcome_switches_model(db):
    service = TaskRecoveryService(db)
    result = _result()
    verification = _verification()

    signature = service.outcome_signature(result.agent_name, result, verification)
    assert signature == service.outcome_signature(
        result.agent_name, result, verification
    )

    failure = service.diagnose(
        agent_result=_contract(),
        result=result,
        verification=verification,
        repeated_outcome=True,
        step_budget_exhausted=False,
    )
    assert failure is not None
    decision = service.plan(failure, attempt_count=0, history=[])

    assert failure.classification == FailureClassification.MODEL_STUCK
    assert decision.action == RecoveryAction.SWITCH_MODEL


def test_explicit_agent_failure_uses_structured_diagnosis(db):
    service = TaskRecoveryService(db)
    failure = service.diagnose(
        agent_result=_contract(
            status="FAILED",
            failure={
                "type": "SyntaxError",
                "message": "compile failed in checkout.py",
            },
        ),
        result=_result(),
        verification=_verification(),
        repeated_outcome=False,
        step_budget_exhausted=False,
    )

    assert failure is not None
    assert failure.classification == FailureClassification.COMPILATION


def test_blocked_agent_expands_context_before_generic_failure_handling(db):
    service = TaskRecoveryService(db)
    result = _result().model_copy(update={"next_state_hint": "blocked"})
    failure = service.diagnose(
        agent_result=_contract(
            status="FAILED",
            failure={"reason": "Need the FraudClient contract."},
        ),
        result=result,
        verification=_verification(),
        repeated_outcome=False,
        step_budget_exhausted=False,
    )
    assert failure is not None
    decision = service.plan(failure, attempt_count=0, history=[])

    assert failure.classification == FailureClassification.MISSING_CONTEXT
    assert decision.action == RecoveryAction.EXPAND_CONTEXT


def test_blocked_infrastructure_failure_retries_instead_of_expanding_context(db):
    service = TaskRecoveryService(db)
    result = _result().model_copy(update={"next_state_hint": "blocked"})
    failure = service.diagnose(
        agent_result=_contract(
            status="FAILED",
            failure={
                "classification": "ENVIRONMENT",
                "type": "ResourceExhaustion",
                "message": "Executor was killed.",
                "details": {"failure_reason": "INFRASTRUCTURE_RESOURCE"},
            },
        ),
        result=result,
        verification=_verification(),
        repeated_outcome=False,
        step_budget_exhausted=False,
    )
    assert failure is not None
    decision = service.plan(failure, attempt_count=0, history=[])

    assert failure.classification == FailureClassification.ENVIRONMENT
    assert decision.action == RecoveryAction.RETRY


def test_outcome_signature_changes_when_patch_changes(db):
    service = TaskRecoveryService(db)
    verification = _verification()
    first = _result().model_copy(
        update={"artifacts": [{"type": "diff", "content": "+first"}]}
    )
    second = _result().model_copy(
        update={"artifacts": [{"type": "diff", "content": "+second"}]}
    )

    assert service.outcome_signature(
        first.agent_name, first, verification
    ) != service.outcome_signature(second.agent_name, second, verification)


def test_outcome_signature_detects_no_progress_across_agents(db):
    service = TaskRecoveryService(db)
    verification = _verification()
    first = _result().model_copy(
        update={
            "agent_name": "CloudExecutor",
            "summary": "Configured verification command failed.",
            "next_state_hint": "debugging",
        }
    )
    second = first.model_copy(
        update={
            "agent_name": "CodexExecutor",
            "summary": "Copilot execution failed on branch sacm/task-1.",
        }
    )

    assert service.outcome_signature(
        first.agent_name, first, verification
    ) == service.outcome_signature(second.agent_name, second, verification)


def test_verification_failure_selects_targeted_repair(db):
    service = TaskRecoveryService(db)
    failure = service.diagnose(
        agent_result=_contract(),
        result=_result(),
        verification=_verification(build_status="FAIL"),
        repeated_outcome=False,
        step_budget_exhausted=False,
    )
    assert failure is not None
    decision = service.plan(failure, attempt_count=0, history=[])

    assert failure.classification == FailureClassification.COMPILATION
    assert decision.action == RecoveryAction.REPAIR_CODE


def test_security_failure_replans(db):
    service = TaskRecoveryService(db)
    verification = _verification().model_copy(update={"security_status": "FAIL"})
    failure = service.diagnose(
        agent_result=_contract(),
        result=_result(),
        verification=verification,
        repeated_outcome=False,
        step_budget_exhausted=False,
    )
    assert failure is not None
    decision = service.plan(failure, attempt_count=0, history=[])

    assert failure.classification == FailureClassification.WRONG_ASSUMPTION
    assert decision.action == RecoveryAction.REPLAN


def test_step_budget_exhaustion_escalates(db):
    service = TaskRecoveryService(db)
    failure = service.diagnose(
        agent_result=_contract(),
        result=_result(),
        verification=_verification(),
        repeated_outcome=False,
        step_budget_exhausted=True,
    )
    assert failure is not None
    decision = service.plan(failure, attempt_count=0, history=[])

    assert failure.classification == FailureClassification.MODEL_STUCK
    assert decision.action == RecoveryAction.ESCALATE
