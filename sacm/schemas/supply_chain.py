from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Sha256Digest = Field(pattern=r"^(?:sha256:)?[0-9a-fA-F]{64}$")


class SupplyChainSubjectV1(BaseModel):
    schema_version: Literal["supply-chain-subject/v1"] = "supply-chain-subject/v1"
    name: str = Field(min_length=1, max_length=512)
    digest: str = Sha256Digest


class SupplyChainRecordCreateV1(BaseModel):
    schema_version: Literal["supply-chain-record/v1"] = "supply-chain-record/v1"
    record_type: Literal[
        "sbom",
        "provenance",
        "dependency_scan",
        "secret_scan",
        "iac_scan",
        "container_scan",
    ]
    format: str = Field(min_length=1, max_length=128)
    subject: SupplyChainSubjectV1
    artifact_sha256: str = Sha256Digest
    status: Literal["PASSED", "FAILED", "COMPLETE", "INCOMPLETE", "VERIFIED"]
    coverage: dict[str, Any] = Field(default_factory=dict)
    content: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    artifact_id: str | None = None
    evidence_pack_id: str | None = None
    image_id: str | None = None
    release_id: str | None = None


class SupplyChainRecordV1(BaseModel):
    id: str
    schema_version: str
    run_id: str
    record_type: str
    format: str
    subject_name: str
    subject_digest: str
    artifact_sha256: str
    status: str
    coverage: dict[str, Any]
    content: dict[str, Any]
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    artifact_id: str | None
    evidence_pack_id: str | None
    image_id: str | None
    release_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProvenanceCreateV1(BaseModel):
    schema_version: Literal["slsa-like-provenance/v1"] = "slsa-like-provenance/v1"
    subject: SupplyChainSubjectV1
    source_repository: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    builder_id: str = Field(min_length=1)
    executor_id: str = Field(min_length=1)
    build_commands: list[str] = Field(min_length=1)
    materials: list[SupplyChainSubjectV1] = Field(default_factory=list)
    products: list[SupplyChainSubjectV1] = Field(default_factory=list)
    agent: str | None = None
    model: str | None = None
    framework: str | None = None
    policy_decisions: list[dict[str, Any]] = Field(default_factory=list)
    security_decisions: list[dict[str, Any]] = Field(default_factory=list)
    snapshot_ids: list[str] = Field(default_factory=list)
    replay_id: str | None = None
    environment: dict[str, Any] = Field(default_factory=dict)
    image_digest: str | None = None


class ImageCreateV1(BaseModel):
    schema_version: Literal["supply-chain-image/v1"] = "supply-chain-image/v1"
    name: str = Field(min_length=1)
    digest: str = Sha256Digest
    repository: str | None = None
    tag: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReleaseCreateV1(BaseModel):
    schema_version: Literal["supply-chain-release/v1"] = "supply-chain-release/v1"
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    digest: str = Sha256Digest
    uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AttestationCreateV1(BaseModel):
    schema_version: Literal["supply-chain-attestation/v1"] = (
        "supply-chain-attestation/v1"
    )
    subject: SupplyChainSubjectV1
    predicate_type: str = Field(min_length=1)
    predicate: dict[str, Any]
    record_id: str | None = None
    artifact_id: str | None = None
    image_id: str | None = None
    release_id: str | None = None
    key_id: str | None = None


class VerificationResultV1(BaseModel):
    schema_version: Literal["verification-result/v1"] = "verification-result/v1"
    status: Literal["VALID", "INVALID", "UNSIGNED"]
    algorithm: str | None = None
    key_id: str | None = None
    public_key_fingerprint: str | None = None
    chain_valid: bool
    errors: list[str] = Field(default_factory=list)


class SupplyChainCompletenessV1(BaseModel):
    schema_version: Literal["supply-chain-completeness/v1"] = (
        "supply-chain-completeness/v1"
    )
    status: Literal["COMPLETE", "INCOMPLETE"]
    mandatory_types: list[str]
    present_types: list[str]
    missing_types: list[str]
