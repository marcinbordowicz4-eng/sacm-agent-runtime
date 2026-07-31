from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from sacm.customer_executor.config import ExecutorSettings
from sacm.schemas.contracts import AgentResultV1


class JobRunner(Protocol):
    def run(self, task: dict, workspace: Path, repository: Path | None) -> AgentResultV1:
        ...


class IsolatedCommandRunner:
    """Invokes an operator-approved sandbox command without shell expansion."""

    def __init__(self, settings: ExecutorSettings) -> None:
        self.settings = settings

    def run(
        self, task: dict, workspace: Path, repository: Path | None
    ) -> AgentResultV1:
        input_path = workspace / "contract.json"
        output_path = workspace / "result.json"
        input_path.write_text(
            json.dumps(task, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(input_path, 0o600)
        replacements = {
            "{input}": str(input_path),
            "{output}": str(output_path),
            "{workspace}": str(workspace),
            "{repository}": str(repository or ""),
        }
        command = [replacements.get(item, item) for item in self.settings.runner_command]
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=int(task["timeout_seconds"]),
                check=False,
                text=False,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(workspace),
                    "SACM_EXECUTOR_NETWORK_BOUNDARY": (
                        self.settings.network_boundary.deployment_type
                    ),
                },
            )
        except subprocess.TimeoutExpired:
            return AgentResultV1(
                run_id=task["run_id"],
                step_id=task["step_id"],
                status="FAILED",
                summary="The isolated customer runner timed out.",
                failure={"type": "IsolatedRunnerTimeout"},
            )
        if completed.returncode != 0 or not output_path.is_file():
            return AgentResultV1(
                run_id=task["run_id"],
                step_id=task["step_id"],
                status="FAILED",
                summary="The isolated customer runner failed.",
                failure={
                    "type": "IsolatedRunnerFailed",
                    "returncode": completed.returncode,
                },
            )
        if output_path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Isolated runner result exceeds the 10 MiB limit.")
        result = AgentResultV1.model_validate_json(output_path.read_bytes())
        return self._sanitize_artifacts(result, workspace)

    def _sanitize_artifacts(
        self, result: AgentResultV1, workspace: Path
    ) -> AgentResultV1:
        root = workspace.resolve()
        for reference in [*result.artifacts, *result.evidence]:
            if not reference.uri:
                if not reference.sha256:
                    raise ValueError("Artifact references must contain a SHA-256 hash.")
                continue
            parsed = urlsplit(reference.uri)
            if parsed.scheme in {"https", "s3", "az"}:
                host = (parsed.hostname or "").lower()
                allowed = {
                    item.lower().split(":", 1)[0]
                    for item in self.settings.network_boundary.outbound_allowlist
                }
                if allowed and host not in allowed:
                    raise ValueError("Artifact upload URI is not boundary-approved.")
                if not reference.sha256:
                    raise ValueError("Uploaded artifacts must include a SHA-256 hash.")
                continue
            path = Path(reference.uri).resolve()
            if root != path and root not in path.parents:
                raise ValueError("Artifact path escaped the isolated workspace.")
            reference.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            reference.uri = None
            reference.metadata["transport"] = "hash-only"
        return result


class WorkspaceManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def create(self, job_id: str) -> Path:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        workspace = self.root / job_id
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(mode=0o700)
        return workspace

    def remove(self, workspace: Path) -> None:
        if workspace.parent.resolve() != self.root.resolve():
            raise ValueError("Refusing to remove a workspace outside the configured root.")
        shutil.rmtree(workspace, ignore_errors=True)


def resolve_repository(settings: ExecutorSettings, task: dict) -> Path | None:
    execution_context = task.get("execution_context") or {}
    coordinate = execution_context.get("repository_coordinate") or execution_context.get(
        "repository_full_name"
    )
    if not coordinate:
        return None
    configured = settings.repository_map.get(str(coordinate))
    if configured is None:
        raise ValueError("The job repository coordinate is not mapped locally.")
    resolved = configured.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("The locally mapped repository is unavailable.")
    return resolved
