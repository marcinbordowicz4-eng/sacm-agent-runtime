from typing import Any, Literal

from pydantic import BaseModel, Field

VerificationStatus = Literal["PASS", "FAIL", "MISSING", "NOT_APPLICABLE"]
EvidenceIntegrity = Literal["VALID", "INVALID", "MISSING"]


class RequirementVerificationV1(BaseModel):
    schema_version: Literal["requirement-verification/v1"] = (
        "requirement-verification/v1"
    )
    requirement_id: str
    requirement_text: str
    status: VerificationStatus
    implementation_references: list[str] = Field(default_factory=list)
    test_references: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    evidence_integrity: EvidenceIntegrity
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class RegressionProofV1(BaseModel):
    schema_version: Literal["regression-proof/v1"] = "regression-proof/v1"
    focused_test_status: VerificationStatus
    failed_before_fix: bool
    affected_area_status: VerificationStatus
    commands: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    status: VerificationStatus


class ContractCompatibilityResultV1(BaseModel):
    schema_version: Literal["contract-compatibility-result/v1"] = (
        "contract-compatibility-result/v1"
    )
    status: VerificationStatus
    checks: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class TestIntegrityResultV1(BaseModel):
    schema_version: Literal["test-integrity-result/v1"] = (
        "test-integrity-result/v1"
    )
    status: VerificationStatus
    tests_removed: list[str] = Field(default_factory=list)
    weakened_assertions: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class VerificationMatrixV2(BaseModel):
    schema_version: Literal["verification-matrix/v2"] = "verification-matrix/v2"
    task_id: str
    run_id: str | None = None
    strict: bool
    build_status: VerificationStatus
    requirements: list[RequirementVerificationV1] = Field(default_factory=list)
    regression: RegressionProofV1
    contract_compatibility: ContractCompatibilityResultV1
    security_status: VerificationStatus
    test_integrity: TestIntegrityResultV1
    technical_complete: bool
    evidence_complete: bool
    complete: bool
    blocking_reasons: list[str] = Field(default_factory=list)
