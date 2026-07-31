from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from sacm.core.execution_signing import canonical_hash, verify_ed25519


class UpdateArtifact(BaseModel):
    platform: str
    uri: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=1)


class UpdateManifest(BaseModel):
    schema_version: Literal["executor-update-manifest/v1"] = (
        "executor-update-manifest/v1"
    )
    current_version: str
    minimum_version: str
    released_at: str
    artifacts: list[UpdateArtifact] = Field(default_factory=list)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    release_notes_uri: str | None = None


class SignedUpdateManifest(BaseModel):
    manifest: UpdateManifest
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str
    signature_metadata: dict[str, str]

    def verify(self) -> None:
        value = self.manifest.model_dump(mode="json")
        if canonical_hash(value) != self.manifest_hash:
            raise ValueError("Update manifest hash verification failed.")
        if self.signature_metadata.get("algorithm") != "Ed25519":
            raise ValueError("Unsupported update manifest signature algorithm.")
        public_key = self.signature_metadata.get("public_key")
        if not public_key:
            raise ValueError("Update manifest signing public key is missing.")
        verify_ed25519(public_key, value, self.signature)


def version_tuple(value: str) -> tuple[int, int, int]:
    core = value.split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Unsupported semantic version: {value}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]
