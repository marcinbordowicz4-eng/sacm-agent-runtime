import base64
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from sacm.core.execution_signing import (
    canonical_hash,
    canonical_json,
    public_key_fingerprint,
)
from sacm.customer_executor.client import ControlPlaneError, ExecutorRevoked
from sacm.customer_executor.config import ExecutorSettings
from sacm.customer_executor.daemon import CustomerExecutorDaemon
from sacm.customer_executor.identity import IdentityStore
from sacm.customer_executor.runner import IsolatedCommandRunner
from sacm.schemas.contracts import AgentResultV1


def _public_key(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def _settings(root: Path, signing_key: Ed25519PrivateKey) -> ExecutorSettings:
    return ExecutorSettings.model_validate(
        {
            "environment": "test",
            "control_plane_url": "https://control.example.internal",
            "state_dir": root / "identity",
            "workspace_root": root / "workspaces",
            "executor_identity": "test-executor",
            "display_name": "Test executor",
            "version": "1.2.3",
            "runner_command": ["approved-runner", "{input}", "{output}"],
            "poll_seconds": 0.1,
            "heartbeat_seconds": 1,
            "network_boundary": {
                "deployment_type": "on-premises",
                "boundary_id": "test-boundary",
                "residency_region": "eu-west",
                "outbound_allowlist": ["control.example.internal"],
                "metadata_service_blocked": True,
                "tls": {
                    "signing_key_sha256": public_key_fingerprint(
                        _public_key(signing_key)
                    )
                },
            },
        }
    )


class FakeClient:
    def __init__(self) -> None:
        self.heartbeat_response: dict[str, Any] = {"control": {}}
        self.lease_response: dict[str, Any] | None = None
        self.started: list[str] = []
        self.completed: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []
        self.lease_heartbeats = 0
        self.closed = False

    def enroll(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.heartbeat_response

    def lease(self, lease_seconds: int) -> dict[str, Any] | None:
        value, self.lease_response = self.lease_response, None
        return value

    def start(self, job_id: str, lease_token: str) -> dict[str, Any]:
        self.started.append(job_id)
        return {}

    def heartbeat_job(
        self, job_id: str, lease_token: str, lease_seconds: int
    ) -> dict[str, Any]:
        self.lease_heartbeats += 1
        return {}

    def complete(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.completed.append(payload)
        return {}

    def fail(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.failed.append(payload)
        return {}

    def rotate(
        self, public_signing_key: str, signing_key_fingerprint: str
    ) -> dict[str, Any]:
        return {}

    def close(self) -> None:
        self.closed = True


class FakeRunner:
    def run(
        self, task: dict[str, Any], workspace: Path, repository: Path | None
    ) -> AgentResultV1:
        return AgentResultV1(
            run_id=task["run_id"],
            step_id=task["step_id"],
            status="COMPLETED",
            summary="completed inside customer boundary",
        )


def test_isolated_runner_returns_bounded_structured_diagnostics(tmp_path):
    signing_key = Ed25519PrivateKey.generate()
    settings = _settings(tmp_path, signing_key).model_copy(
        update={
            "runner_command": [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.stdout.write('x' * 70000); "
                    "sys.stderr.write("
                    "'\\nFAILED tests/test_api.py::test_create - assert 1 == 2'); "
                    "sys.exit(1)"
                ),
            ]
        }
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = IsolatedCommandRunner(settings).run(
        {
            "run_id": "run-1",
            "step_id": "step-1",
            "timeout_seconds": 60,
        },
        workspace,
        None,
    )

    assert result.status == "FAILED"
    failure = (
        result.failure.model_dump(mode="json")
        if hasattr(result.failure, "model_dump")
        else result.failure
    )
    bundle = failure["diagnostic_bundle"]
    assert bundle["exit_code"] == 1
    assert bundle["tool"] == Path(sys.executable).name
    assert len(bundle["raw_output"]) <= 65536
    assert "FAILED tests/test_api.py::test_create" in bundle["raw_output"]


def _lease(
    control_key: Ed25519PrivateKey, *, objective: str = "private source context"
) -> dict[str, Any]:
    task = {
        "schema_version": "agent-task/v1",
        "run_id": "run-1",
        "step_id": "step-1",
        "role": "reasoner",
        "objective": objective,
        "acceptance_criteria": [],
        "context_references": ["context:approved"],
        "allowed_tools": [],
        "denied_tools": [],
        "token_budget": 100,
        "cost_budget_usd": None,
        "timeout_seconds": 60,
        "output_schema": "agent-result/v1",
        "execution_context": {
            "repository_coordinate": "customer/repository",
            "workspace_location": "customer-managed",
        },
    }
    public_key = _public_key(control_key)
    return {
        "job": {"id": "job-1"},
        "lease_token": "lease-token-that-is-never-logged-1234567890",
        "payload_contract": task,
        "payload_hash": canonical_hash(task),
        "payload_signature": base64.b64encode(
            control_key.sign(canonical_json(task))
        ).decode(),
        "payload_signature_metadata": {
            "algorithm": "Ed25519",
            "key_fingerprint": public_key_fingerprint(public_key),
            "public_key": public_key,
        },
    }


def test_identity_files_are_restrictive_and_detect_permission_tampering(tmp_path):
    identity = IdentityStore(tmp_path / "identity")
    identity.initialize()
    identity.write_token("executor-secret-token")

    assert stat.S_IMODE(identity.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(identity.private_key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(identity.token_path.stat().st_mode) == 0o600

    os.chmod(identity.token_path, 0o644)
    with pytest.raises(PermissionError, match="0600"):
        identity.token()


def test_production_rejects_insecure_control_plane_and_metadata_access(tmp_path):
    base = {
        "environment": "production",
        "control_plane_url": "http://control.example.internal",
        "state_dir": tmp_path / "identity",
        "workspace_root": tmp_path / "workspaces",
        "executor_identity": "executor",
        "display_name": "Executor",
        "version": "1.0.0",
        "runner_command": ["approved-runner"],
        "network_boundary": {
            "deployment_type": "vpc",
            "boundary_id": "vpc-1",
            "residency_region": "eu",
            "metadata_service_blocked": True,
            "tls": {
                "signing_key_sha256": "a" * 64,
                "server_certificate_sha256": "b" * 64,
            },
        },
    }
    with pytest.raises(ValidationError, match="insecure HTTP"):
        ExecutorSettings.model_validate(base)

    metadata = {
        **base,
        "control_plane_url": "https://169.254.169.254",
        "network_boundary": {
            **base["network_boundary"],
            "outbound_allowlist": ["169.254.169.254"],
        },
    }
    with pytest.raises(ValidationError, match="metadata"):
        ExecutorSettings.model_validate(metadata)


def test_config_file_supports_nested_environment_overrides(tmp_path, monkeypatch):
    config = tmp_path / "executor.yaml"
    config.write_text(
        """
environment: test
control_plane_url: https://control.example.internal
state_dir: ./identity
workspace_root: ./workspaces
executor_identity: executor
display_name: Executor
version: 1.0.0
network_boundary:
  deployment_type: on-premises
  boundary_id: boundary
  residency_region: original
  outbound_allowlist: [control.example.internal]
  metadata_service_blocked: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "SACM_EXECUTOR_NETWORK_BOUNDARY__RESIDENCY_REGION", "overridden"
    )
    monkeypatch.setenv("SACM_EXECUTOR_CAPABILITIES", '["agent-task/v1", "evidence/v1"]')

    settings = ExecutorSettings.load(config)

    assert settings.network_boundary.residency_region == "overridden"
    assert settings.capabilities == ["agent-task/v1", "evidence/v1"]


def test_public_boundary_metadata_excludes_tls_file_paths(tmp_path):
    control_key = Ed25519PrivateKey.generate()
    settings = _settings(tmp_path, control_key)

    metadata = settings.network_boundary.public_metadata()

    assert "ca_bundle" not in metadata["tls"]
    assert "client_key" not in metadata["tls"]
    assert "client_certificate" not in metadata["tls"]


def test_verified_job_runs_without_payload_or_secret_logs(tmp_path, caplog):
    control_key = Ed25519PrivateKey.generate()
    settings = _settings(tmp_path, control_key)
    settings.repository_map["customer/repository"] = tmp_path
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    identity.write_token("executor-secret-token")
    client = FakeClient()
    daemon = CustomerExecutorDaemon(
        settings, identity, client, runner=FakeRunner()
    )
    lease = _lease(control_key, objective="TOP-SECRET-PAYLOAD-CONTENT")

    with caplog.at_level(logging.INFO):
        daemon._process_lease(lease)

    assert client.started == ["job-1"]
    assert len(client.completed) == 1
    assert client.completed[0]["result_hash"] == canonical_hash(
        client.completed[0]["result"]
    )
    logs = caplog.text
    assert "TOP-SECRET-PAYLOAD-CONTENT" not in logs
    assert "executor-secret-token" not in logs
    assert lease["lease_token"] not in logs
    assert not (settings.workspace_root / "job-1").exists()


def test_tampered_payload_and_signing_key_are_rejected_before_execution(tmp_path):
    control_key = Ed25519PrivateKey.generate()
    settings = _settings(tmp_path, control_key)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    client = FakeClient()
    daemon = CustomerExecutorDaemon(
        settings, identity, client, runner=FakeRunner()
    )
    lease = _lease(control_key)
    lease["payload_contract"]["objective"] = "tampered"

    with pytest.raises(ControlPlaneError, match="hash"):
        daemon._process_lease(lease)
    assert client.started == []

    other_key = Ed25519PrivateKey.generate()
    lease = _lease(other_key)
    with pytest.raises(ControlPlaneError, match="fingerprint"):
        daemon._process_lease(lease)
    assert client.started == []


def test_tampered_update_drains_on_minimum_version_and_rejects_signature(tmp_path):
    control_key = Ed25519PrivateKey.generate()
    settings = _settings(tmp_path, control_key)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    client = FakeClient()
    daemon = CustomerExecutorDaemon(settings, identity, client, runner=FakeRunner())
    manifest = {
        "schema_version": "executor-update-manifest/v1",
        "current_version": "2.0.0",
        "minimum_version": "1.3.0",
        "released_at": "2026-01-01T00:00:00Z",
        "artifacts": [],
        "compatibility": {"job_contracts": ["agent-task/v1"]},
        "release_notes_uri": None,
    }
    public_key = _public_key(control_key)
    client.heartbeat_response = {
        "control": {
            "minimum_version": "1.3.0",
            "update_manifest": {
                "manifest": manifest,
                "manifest_hash": canonical_hash(manifest),
                "signature": base64.b64encode(
                    control_key.sign(canonical_json(manifest))
                ).decode(),
                "signature_metadata": {
                    "algorithm": "Ed25519",
                    "key_fingerprint": public_key_fingerprint(public_key),
                    "public_key": public_key,
                },
            },
        }
    }
    daemon._heartbeat()
    assert identity.draining

    identity.set_drain(False)
    client.heartbeat_response["control"]["update_manifest"]["manifest_hash"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        daemon._heartbeat()


def test_revocation_and_retry_backoff_are_fail_closed(tmp_path):
    control_key = Ed25519PrivateKey.generate()
    settings = _settings(tmp_path, control_key)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()

    class RevokedClient(FakeClient):
        def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise ExecutorRevoked("revoked")

    revoked_client = RevokedClient()
    daemon = CustomerExecutorDaemon(
        settings, identity, revoked_client, runner=FakeRunner()
    )
    daemon._start_health_server = lambda: None  # type: ignore[method-assign]
    daemon.run()
    assert daemon.revoked
    assert identity.draining

    identity.set_drain(False)

    class RetryClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            raise ControlPlaneError("temporary")

    retry_client = RetryClient()
    delays = []
    retry_daemon = CustomerExecutorDaemon(
        settings,
        identity,
        retry_client,
        runner=FakeRunner(),
        sleep=lambda delay: (
            delays.append(delay),
            retry_daemon.stop_event.set() if len(delays) == 2 else None,
        ),
    )
    retry_daemon._start_health_server = lambda: None  # type: ignore[method-assign]
    retry_daemon.run()
    assert delays == [settings.retry_initial_seconds, settings.retry_initial_seconds * 2]


def test_lease_heartbeat_uses_injected_client(tmp_path):
    control_key = Ed25519PrivateKey.generate()
    settings = _settings(tmp_path, control_key)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    client = FakeClient()
    daemon = CustomerExecutorDaemon(settings, identity, client, runner=FakeRunner())

    class StopAfterOne:
        calls = 0

        def wait(self, timeout: float) -> bool:
            self.calls += 1
            return self.calls > 1

    daemon._lease_heartbeat_loop("job", "lease-token", StopAfterOne())  # type: ignore[arg-type]
    assert client.lease_heartbeats == 1
