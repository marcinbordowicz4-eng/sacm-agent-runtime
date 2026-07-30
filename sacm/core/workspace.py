import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sacm.adapters.repository_adapter import RepositoryAdapter
from sacm.core.policy_service import PolicyService, ToolGateway


@dataclass(frozen=True)
class WorkspaceRef:
    run_id: str
    repository_path: str
    path: str
    branch_name: str


class WorkspaceManager:
    """Creates per-run worktrees and executes commands only in constrained Docker."""

    def __init__(self, policies: PolicyService | None = None) -> None:
        self.gateway = ToolGateway(policies or PolicyService())

    def create(self, run_id: str, repository_path: str) -> WorkspaceRef:
        branch_name = f"sacm/{run_id[:12]}/workspace"
        adapter = RepositoryAdapter(repository_path)
        path = adapter.create_worktree(branch_name)
        return WorkspaceRef(
            run_id=run_id,
            repository_path=str(adapter.repo_path),
            path=path,
            branch_name=branch_name,
        )

    def execute(
        self,
        workspace: WorkspaceRef,
        image: str,
        command: list[str],
        *,
        timeout_seconds: int = 600,
        cpu_limit: float = 2.0,
        memory_limit_mb: int = 4096,
        pids_limit: int = 256,
    ) -> dict:
        if not image:
            raise ValueError("A sandbox image is required.")
        if not command or any(not part for part in command):
            raise ValueError("Command must contain non-empty arguments.")
        if timeout_seconds <= 0 or cpu_limit <= 0 or memory_limit_mb <= 0 or pids_limit <= 0:
            raise ValueError("Sandbox limits must be positive.")
        runtime = os.getenv("SACM_DOCKER_RUNTIME", "runc")
        if runtime not in {"runc", "runsc"}:
            raise ValueError("SACM_DOCKER_RUNTIME must be runc or runsc.")
        try:
            self.gateway.authorize(
                "workspace.execute",
                {"image": image, "command": command, "path": workspace.path},
                run_id=workspace.run_id,
            )
        except PermissionError as exc:
            return {"returncode": 126, "stdout": "", "stderr": str(exc)}

        docker_command = [
            "docker",
            "run",
            "--rm",
            *(["--runtime", runtime] if runtime == "runsc" else []),
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "1000:1000",
            "--cpus",
            str(cpu_limit),
            "--memory",
            f"{memory_limit_mb}m",
            "--pids-limit",
            str(pids_limit),
            "--volume",
            f"{Path(workspace.path).resolve()}:/workspace:rw",
            "--workdir",
            "/workspace",
            image,
            *command,
        ]
        try:
            result = subprocess.run(
                docker_command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            return {"returncode": 127, "stdout": "", "stderr": "docker is unavailable"}
        except subprocess.TimeoutExpired:
            return {
                "returncode": 124,
                "stdout": "",
                "stderr": f"Sandbox timed out after {timeout_seconds} seconds.",
            }
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-20_000:],
            "stderr": result.stderr[-8_000:],
        }

    def destroy(self, workspace: WorkspaceRef) -> None:
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", workspace.path],
            cwd=workspace.repository_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to remove workspace: {result.stderr.strip() or result.stdout.strip()}"
            )
