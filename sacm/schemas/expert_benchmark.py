from typing import Literal

from pydantic import BaseModel, Field


class ExpertBenchmarkProductV1(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    autonomous_coding: float = Field(ge=1, le=5)
    governance: float = Field(ge=1, le=5)
    vendor_neutral: float = Field(ge=1, le=5)
    evidence_audit: float = Field(ge=1, le=5)
    ux_maturity: float = Field(ge=1, le=5)
    overall: float = Field(ge=1, le=5)


class ExpertBenchmarkAssessmentV1(BaseModel):
    schema_version: Literal["expert-benchmark-assessment/v1"] = (
        "expert-benchmark-assessment/v1"
    )
    assessment_type: Literal["expert_opinion"] = "expert_opinion"
    disclaimer: str = Field(min_length=1, max_length=500)
    as_of: str = Field(min_length=1, max_length=32)
    products: list[ExpertBenchmarkProductV1] = Field(min_length=1, max_length=30)
    updated_at: str | None = None
    updated_by: str | None = None
