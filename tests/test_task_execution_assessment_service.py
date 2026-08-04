import pytest

from sacm.core.task_execution_assessment_service import (
    TaskExecutionAssessmentService,
)


def test_typecheck_task_uses_focused_verification():
    assessment = TaskExecutionAssessmentService().assess(
        "Fix current TypeScript errors and run typecheck.",
        requested_max_steps=10,
    )

    assert assessment.mode == "focused_verification"
    assert assessment.max_steps == 2
    assert assessment.include_test_command is False


def test_explicit_lint_request_uses_standard_validation():
    assessment = TaskExecutionAssessmentService().assess(
        "Fix TypeScript errors, then run typecheck and lint.",
        requested_max_steps=10,
    )

    assert assessment.mode == "standard"
    assert assessment.max_steps == 10
    assert assessment.include_test_command is True


def test_assessment_rejects_invalid_step_budget():
    with pytest.raises(ValueError, match="positive"):
        TaskExecutionAssessmentService().assess(
            "Run TypeScript typecheck.", requested_max_steps=0
        )
