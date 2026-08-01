from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class FailureClassification(StrEnum):
    COMPILATION = "COMPILATION"
    TEST_REGRESSION = "TEST_REGRESSION"
    WRONG_ASSUMPTION = "WRONG_ASSUMPTION"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    ARCHITECTURE_MISMATCH = "ARCHITECTURE_MISMATCH"
    BAD_PLAN = "BAD_PLAN"
    API_INCOMPATIBILITY = "API_INCOMPATIBILITY"
    ENVIRONMENT = "ENVIRONMENT"
    TOOL_FAILURE = "TOOL_FAILURE"
    MODEL_STUCK = "MODEL_STUCK"


class RecoveryAction(StrEnum):
    REPAIR_CODE = "REPAIR_CODE"
    DEBUG = "DEBUG"
    REPLAN = "REPLAN"
    EXPAND_CONTEXT = "EXPAND_CONTEXT"
    SWITCH_MODEL = "SWITCH_MODEL"
    RETRY = "RETRY"
    ESCALATE = "ESCALATE"


class DiagnosticEvidenceV2(BaseModel):
    kind: Literal[
        "compiler",
        "test",
        "stack_trace",
        "environment",
        "contract",
        "tool",
        "requirement",
        "history",
    ]
    source: str
    message: str
    code: str | None = None
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    test_name: str | None = None
    requirement_id: str | None = None


class DiagnosticBundleV2(BaseModel):
    schema_version: Literal["diagnostic-bundle/v2"] = "diagnostic-bundle/v2"
    command: str | None = None
    exit_code: int | None = None
    tool: str | None = None
    raw_output: str | None = None
    compiler_diagnostics: list[DiagnosticEvidenceV2] = Field(default_factory=list)
    failed_tests: list[DiagnosticEvidenceV2] = Field(default_factory=list)
    stack_traces: list[DiagnosticEvidenceV2] = Field(default_factory=list)
    changed_symbols: list[str] = Field(default_factory=list)
    affected_requirements: list[str] = Field(default_factory=list)
    environment_errors: list[DiagnosticEvidenceV2] = Field(default_factory=list)
    previous_attempts: list[dict[str, Any]] = Field(default_factory=list)
    graph_context: dict[str, Any] = Field(default_factory=dict)
    root_cause_analysis: dict[str, Any] | None = None
    patch_hash: str | None = None


class FailureInputV1(BaseModel):
    schema_version: Literal["failure-input/v1"] = "failure-input/v1"
    classification: FailureClassification | None = None
    type: str = "AgentFailure"
    message: str = Field(min_length=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    diagnostic_bundle: DiagnosticBundleV2 | None = None


class FailureReportV1(BaseModel):
    schema_version: Literal["failure-report/v1"] = "failure-report/v1"
    classification: FailureClassification
    type: str
    message: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool
    confidence: float = Field(ge=0, le=1)
    root_cause: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    diagnosis_fingerprint: str | None = None
    diagnostic_bundle: DiagnosticBundleV2 | None = None
    stages: list[str] = Field(default_factory=list)


class RecoveryDecisionV1(BaseModel):
    schema_version: Literal["recovery-decision/v1"] = "recovery-decision/v1"
    action: RecoveryAction
    status: Literal["SCHEDULED", "ESCALATED"]
    reason: str
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=0)
    target_run_status: str
    instructions: list[str] = Field(default_factory=list)


class RecoveryStateV1(BaseModel):
    schema_version: Literal["recovery-state/v1"] = "recovery-state/v1"
    status: Literal["IDLE", "SCHEDULED", "ESCALATED"]
    attempt_count: int = Field(ge=0)
    last_step_id: str | None = None
    last_failure: FailureReportV1 | None = None
    last_decision: RecoveryDecisionV1 | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class RecoveryApplyV1(BaseModel):
    step_id: str
    action: RecoveryAction | None = None
