from __future__ import annotations

import hmac
import json
import logging
import signal
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from sacm.core.execution_signing import (
    canonical_hash,
    public_key_fingerprint,
    verify_ed25519,
)
from sacm.customer_executor.client import (
    ControlPlaneClient,
    ControlPlaneError,
    ExecutorRevoked,
)
from sacm.customer_executor.config import ExecutorSettings
from sacm.customer_executor.identity import IdentityStore
from sacm.customer_executor.runner import (
    IsolatedCommandRunner,
    JobRunner,
    WorkspaceManager,
    resolve_repository,
)
from sacm.customer_executor.update import SignedUpdateManifest, version_tuple
from sacm.schemas.contracts import AgentResultV1, AgentTaskV1

LOGGER = logging.getLogger("sacm.customer_executor")


class CustomerExecutorDaemon:
    def __init__(
        self,
        settings: ExecutorSettings,
        identity: IdentityStore,
        client: ControlPlaneClient,
        *,
        runner: JobRunner | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.identity = identity
        self.client = client
        self.runner = runner or IsolatedCommandRunner(settings)
        self.workspaces = WorkspaceManager(settings.workspace_root)
        self.sleep = sleep
        self.stop_event = threading.Event()
        self.revoked = False
        self.active_job_id: str | None = None
        self.last_heartbeat: str | None = None
        self.last_error: str | None = None
        self._health_server: ThreadingHTTPServer | None = None

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop("SIGTERM"))
        signal.signal(signal.SIGINT, lambda *_: self.stop("SIGINT"))

    def stop(self, reason: str = "shutdown") -> None:
        self.identity.set_drain(True, reason)
        self.stop_event.set()

    def run(self, *, once: bool = False) -> None:
        self.identity.initialize()
        self.identity.assert_secure_permissions()
        self._start_health_server()
        backoff = self.settings.retry_initial_seconds
        try:
            while not self.stop_event.is_set():
                try:
                    control = self._heartbeat()
                    backoff = self.settings.retry_initial_seconds
                    if not self.identity.draining and not control.get("drain", False):
                        lease = self.client.lease(self.settings.lease_seconds)
                        if lease:
                            self._process_lease(lease)
                    if once:
                        return
                    self.sleep(self.settings.poll_seconds)
                except ExecutorRevoked:
                    self.revoked = True
                    self.identity.set_drain(True, "control-plane revocation")
                    self.stop_event.set()
                except (ControlPlaneError, OSError, ValueError) as exc:
                    self.last_error = type(exc).__name__
                    LOGGER.warning("Control-plane operation failed; retrying.")
                    if once:
                        raise
                    self.sleep(backoff)
                    backoff = min(backoff * 2, self.settings.retry_max_seconds)
        finally:
            if self._health_server:
                self._health_server.shutdown()
            self.client.close()

    def status(self) -> dict[str, Any]:
        return {
            "status": (
                "revoked"
                if self.revoked
                else "draining"
                if self.identity.draining
                else "busy"
                if self.active_job_id
                else "ready"
            ),
            "executor_identity": self.settings.executor_identity,
            "version": self.settings.version,
            "active_job_id": self.active_job_id,
            "last_heartbeat": self.last_heartbeat,
            "last_error": self.last_error,
            "capacity": self.settings.capacity.model_dump(mode="json"),
            "network_boundary": {
                "deployment_type": self.settings.network_boundary.deployment_type,
                "boundary_id": self.settings.network_boundary.boundary_id,
                "residency_region": self.settings.network_boundary.residency_region,
            },
        }

    def _heartbeat(self) -> dict[str, Any]:
        response = self.client.heartbeat(
            {
                "version": self.settings.version,
                "network_boundary": self.settings.network_boundary.public_metadata(),
                "capabilities": self.settings.capabilities,
                "labels": self.settings.labels,
                "capacity": {
                    **self.settings.capacity.model_dump(mode="json"),
                    "active_jobs": int(self.active_job_id is not None),
                    "draining": self.identity.draining,
                },
            }
        )
        self.last_heartbeat = datetime.now(timezone.utc).isoformat()
        self.last_error = None
        control = response.get("control") or {}
        if control.get("revoked"):
            raise ExecutorRevoked("Executor was revoked by the control plane.")
        if control.get("drain"):
            self.identity.set_drain(True, str(control.get("reason") or "control-plane drain"))
        minimum = control.get("minimum_version")
        if minimum and version_tuple(self.settings.version) < version_tuple(minimum):
            self.identity.set_drain(True, "minimum version enforcement")
        signed_manifest = control.get("update_manifest")
        if signed_manifest:
            manifest = SignedUpdateManifest.model_validate(signed_manifest)
            self._verify_signing_fingerprint(manifest.signature_metadata)
            manifest.verify()
            compatibility = manifest.manifest.compatibility
            if (
                "agent-task/v1"
                not in compatibility.get("job_contracts", ["agent-task/v1"])
                or "agent-result/v1"
                not in compatibility.get("result_contracts", ["agent-result/v1"])
            ):
                self.identity.set_drain(True, "update contract incompatibility")
            if version_tuple(self.settings.version) < version_tuple(
                manifest.manifest.minimum_version
            ):
                self.identity.set_drain(True, "update compatibility enforcement")
        return control

    def _process_lease(self, lease: dict[str, Any]) -> None:
        task_data = lease["payload_contract"]
        expected_hash = lease["payload_hash"]
        if not hmac.compare_digest(canonical_hash(task_data), expected_hash):
            raise ControlPlaneError("Leased job payload hash verification failed.")
        signature_metadata = lease["payload_signature_metadata"]
        public_key = signature_metadata.get("public_key")
        if (
            signature_metadata.get("algorithm") != "Ed25519"
            or not public_key
            or public_key_fingerprint(public_key)
            != signature_metadata.get("key_fingerprint")
        ):
            raise ControlPlaneError("Leased job signing metadata is invalid.")
        self._verify_signing_fingerprint(signature_metadata)
        verify_ed25519(public_key, task_data, lease["payload_signature"])
        task = AgentTaskV1.model_validate(task_data)
        job_id = str(lease["job"]["id"])
        lease_token = str(lease["lease_token"])
        workspace = self.workspaces.create(job_id)
        self.active_job_id = job_id
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._lease_heartbeat_loop,
            args=(job_id, lease_token, heartbeat_stop),
            daemon=True,
        )
        try:
            self.client.start(job_id, lease_token)
            heartbeat.start()
            repository = resolve_repository(self.settings, task_data)
            result = self.runner.run(task.model_dump(mode="json"), workspace, repository)
            self._validate_result(result, task)
            submission = self._signed_result(lease_token, result)
            if result.status == "FAILED":
                self.client.fail(job_id, submission)
            else:
                self.client.complete(job_id, submission)
        except (ValueError, OSError) as exc:
            result = AgentResultV1(
                run_id=task.run_id,
                step_id=task.step_id,
                status="FAILED",
                summary="Customer executor rejected or failed the isolated job.",
                failure={"type": type(exc).__name__},
            )
            self.client.fail(job_id, self._signed_result(lease_token, result))
        finally:
            heartbeat_stop.set()
            if heartbeat.is_alive():
                heartbeat.join(timeout=self.settings.heartbeat_seconds + 1)
            self.active_job_id = None
            self.workspaces.remove(workspace)

    def _lease_heartbeat_loop(
        self, job_id: str, lease_token: str, stop: threading.Event
    ) -> None:
        while not stop.wait(self.settings.heartbeat_seconds):
            try:
                self.client.heartbeat_job(
                    job_id, lease_token, self.settings.lease_seconds
                )
            except ExecutorRevoked:
                self.revoked = True
                self.identity.set_drain(True, "revoked during active lease")
                self.stop_event.set()
                return
            except ControlPlaneError:
                self.last_error = "LeaseHeartbeatFailed"
                LOGGER.warning("Lease heartbeat failed; execution remains isolated.")

    def _signed_result(
        self, lease_token: str, result: AgentResultV1
    ) -> dict[str, Any]:
        result_data = result.model_dump(mode="json")
        return {
            "lease_token": lease_token,
            "result": result_data,
            "result_hash": canonical_hash(result_data),
            "signature": self.identity.sign(result_data),
            "signature_algorithm": "Ed25519",
            "signing_key_fingerprint": self.identity.fingerprint(),
        }

    @staticmethod
    def _validate_result(result: AgentResultV1, task: AgentTaskV1) -> None:
        if result.run_id != task.run_id or result.step_id != task.step_id:
            raise ValueError("Runner result identity does not match the leased job.")
        for reference in [*result.artifacts, *result.evidence]:
            if not reference.sha256 or len(reference.sha256) != 64:
                raise ValueError("Runner results may return only hash-addressed evidence.")

    def _verify_signing_fingerprint(self, metadata: dict[str, str]) -> None:
        expected = self.settings.network_boundary.tls.signing_key_sha256
        if expected and not hmac.compare_digest(
            str(metadata.get("key_fingerprint", "")).lower(), expected.lower()
        ):
            raise ControlPlaneError("Control-plane signing key fingerprint mismatch.")

    def _start_health_server(self) -> None:
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path not in {"/health", "/status", "/capacity"}:
                    self.send_error(404)
                    return
                value = daemon.status()
                if self.path == "/capacity":
                    value = value["capacity"]
                payload = json.dumps(value, sort_keys=True).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._health_server = ThreadingHTTPServer(
            (self.settings.health_bind, self.settings.health_port), Handler
        )
        threading.Thread(target=self._health_server.serve_forever, daemon=True).start()
