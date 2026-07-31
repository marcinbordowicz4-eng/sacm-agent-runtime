from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TLSMetadata(BaseModel):
    ca_bundle: Path | None = None
    client_certificate: Path | None = None
    client_key: Path | None = None
    server_certificate_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    signing_key_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    client_certificate_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )

    @model_validator(mode="after")
    def validate_mtls(self) -> "TLSMetadata":
        if bool(self.client_certificate) != bool(self.client_key):
            raise ValueError("mTLS client_certificate and client_key must be set together.")
        if self.client_key and self.client_key.exists():
            if self.client_key.is_symlink():
                raise ValueError("mTLS client_key cannot be a symlink.")
            if stat.S_IMODE(self.client_key.stat().st_mode) & 0o077:
                raise ValueError("mTLS client_key permissions must be 0600.")
        return self


class NetworkBoundary(BaseModel):
    schema_version: Literal["executor-network-boundary/v1"] = (
        "executor-network-boundary/v1"
    )
    deployment_type: Literal["vpc", "vnet", "on-premises", "air-gapped"]
    boundary_id: str = Field(min_length=1, max_length=255)
    residency_region: str = Field(min_length=1, max_length=100)
    outbound_allowlist: list[str] = Field(default_factory=list)
    proxy_url: str | None = None
    metadata_service_blocked: bool = True
    private_control_plane: bool = False
    tls: TLSMetadata = Field(default_factory=TLSMetadata)

    @model_validator(mode="after")
    def validate_boundary(self) -> "NetworkBoundary":
        forbidden = {
            "169.254.169.254",
            "metadata.google.internal",
            "100.100.100.200",
        }
        normalized = {item.lower().split(":", 1)[0] for item in self.outbound_allowlist}
        if forbidden & normalized:
            raise ValueError("Cloud metadata services cannot be outbound allowlisted.")
        if self.deployment_type == "air-gapped" and self.outbound_allowlist:
            raise ValueError("Air-gapped executors cannot declare outbound destinations.")
        if self.proxy_url:
            proxy = urlsplit(self.proxy_url)
            if proxy.scheme not in {"http", "https"} or not proxy.hostname:
                raise ValueError("proxy_url must be an absolute HTTP(S) URL.")
            if proxy.username or proxy.password:
                raise ValueError("Proxy credentials must not be stored in boundary metadata.")
        return self

    def public_metadata(self) -> dict[str, Any]:
        client_fingerprint = self.tls.client_certificate_sha256
        if self.tls.client_certificate and self.tls.client_certificate.is_file():
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes

            certificate = x509.load_pem_x509_certificate(
                self.tls.client_certificate.read_bytes()
            )
            client_fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
        return {
            "schema_version": self.schema_version,
            "deployment_type": self.deployment_type,
            "boundary_id": self.boundary_id,
            "residency_region": self.residency_region,
            "outbound_allowlist": self.outbound_allowlist,
            "proxy_url": self.proxy_url,
            "metadata_service_blocked": self.metadata_service_blocked,
            "private_control_plane": self.private_control_plane,
            "tls": {
                "server_certificate_sha256": self.tls.server_certificate_sha256,
                "signing_key_sha256": self.tls.signing_key_sha256,
                "mtls": bool(
                    self.tls.client_certificate and self.tls.client_key
                ),
                "client_certificate_sha256": client_fingerprint,
            },
        }


class CapacitySettings(BaseModel):
    max_concurrent_jobs: int = Field(default=1, ge=1, le=128)
    cpu_units: int | None = Field(default=None, ge=1)
    memory_mb: int | None = Field(default=None, ge=64)
    workspace_bytes: int | None = Field(default=None, ge=1)


class ExecutorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SACM_EXECUTOR_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    environment: Literal["development", "test", "production"] = "production"
    control_plane_url: str
    state_dir: Path = Path("/var/lib/sacm-executor/identity")
    workspace_root: Path = Path("/var/lib/sacm-executor/workspaces")
    executor_identity: str = Field(pattern=r"^[a-zA-Z0-9_.:@/-]+$")
    display_name: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    runtime_kind: str = "customer-hosted"
    sandbox_runtime: str = "runsc"
    sandbox_verified: bool = True
    capabilities: list[str] = Field(default_factory=lambda: ["agent-task/v1"])
    labels: dict[str, str] = Field(default_factory=dict)
    storage_classification: Literal[
        "Public", "Internal", "Confidential", "Restricted"
    ] = "Confidential"
    storage_class: str = "customer-managed"
    network_boundary: NetworkBoundary
    capacity: CapacitySettings = Field(default_factory=CapacitySettings)
    runner_command: list[str] = Field(default_factory=list)
    repository_map: dict[str, Path] = Field(default_factory=dict)
    poll_seconds: float = Field(default=5.0, ge=0.1, le=300)
    heartbeat_seconds: float = Field(default=30.0, ge=1, le=300)
    lease_seconds: int = Field(default=120, ge=15, le=3600)
    request_timeout_seconds: float = Field(default=30, ge=1, le=300)
    retry_initial_seconds: float = Field(default=1, ge=0.1, le=60)
    retry_max_seconds: float = Field(default=60, ge=1, le=900)
    health_bind: str = "127.0.0.1"
    health_port: int = Field(default=8787, ge=1, le=65535)

    @model_validator(mode="after")
    def validate_security(self) -> "ExecutorSettings":
        parsed = urlsplit(self.control_plane_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("control_plane_url must be an absolute HTTP(S) URL.")
        if self.environment == "production" and parsed.scheme != "https":
            raise ValueError("Production executors refuse insecure HTTP control planes.")
        if self.environment == "production" and not self.network_boundary.metadata_service_blocked:
            raise ValueError("Production executors must block cloud metadata services.")
        if (
            self.environment == "production"
            and not self.network_boundary.tls.signing_key_sha256
        ):
            raise ValueError(
                "Production executors require a pinned control-plane signing key."
            )
        if (
            self.environment == "production"
            and not self.network_boundary.tls.server_certificate_sha256
        ):
            raise ValueError(
                "Production executors require a pinned control-plane TLS certificate."
            )
        host = parsed.hostname.lower()
        if host in {"169.254.169.254", "metadata.google.internal", "100.100.100.200"}:
            raise ValueError("The control plane cannot be a cloud metadata endpoint.")
        if self.network_boundary.deployment_type != "air-gapped":
            allowlist = {item.lower().split(":", 1)[0] for item in self.network_boundary.outbound_allowlist}
            if allowlist and host not in allowlist:
                raise ValueError("The control-plane host is not in the outbound allowlist.")
        if self.environment == "production" and not self.runner_command:
            raise ValueError("Production executors require a fixed isolation runner_command.")
        return self

    @classmethod
    def load(cls, path: str | Path) -> "ExecutorSettings":
        config_path = Path(path).expanduser().resolve()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("Executor configuration must be a YAML mapping.")
        data = dict(raw)
        prefix = "SACM_EXECUTOR_"
        for env_name, raw_value in os.environ.items():
            if not env_name.startswith(prefix):
                continue
            path_parts = [
                part.lower()
                for part in env_name.removeprefix(prefix).split("__")
            ]
            if path_parts[0] not in cls.model_fields:
                continue
            try:
                value: Any = yaml.safe_load(raw_value)
            except yaml.YAMLError as exc:
                raise ValueError(f"{env_name} is not valid YAML/JSON.") from exc
            target = data
            for part in path_parts[:-1]:
                nested = target.setdefault(part, {})
                if not isinstance(nested, dict):
                    nested = {}
                    target[part] = nested
                target = nested
            target[path_parts[-1]] = value
        return cls.model_validate(data)

    def public_status(self) -> dict[str, Any]:
        return {
            "executor_identity": self.executor_identity,
            "display_name": self.display_name,
            "version": self.version,
            "runtime_kind": self.runtime_kind,
            "sandbox_runtime": self.sandbox_runtime,
            "capabilities": self.capabilities,
            "labels": self.labels,
            "network_boundary": self.network_boundary.public_metadata(),
            "capacity": self.capacity.model_dump(mode="json"),
        }
