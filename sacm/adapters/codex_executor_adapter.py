import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from sacm.adapters.repository_adapter import RepositoryAdapter
from sacm.core.verification_execution import (
    resource_failure_reason,
    sequential_retry_command,
)


class CodexExecutorAdapter:
    """Runs Codex in an isolated Git worktree and returns execution evidence."""

    def __init__(self, repo_path: str) -> None:
        self.repository = RepositoryAdapter(repo_path)

    def execute(
        self,
        task_id: str,
        prompt: str,
        verification_commands: list[str],
    ) -> dict[str, Any]:
        branch_name = self._branch_name(task_id)
        worktree_path = self.repository.create_worktree(branch_name)
        codex = self._run(
            ["codex", "exec", "--full-auto", "--json", prompt],
            worktree_path,
            timeout=1_800,
        )
        dependency_bin = self.repository.repo_path / "node_modules" / ".bin"
        verification = [
            self._run_verification(command, worktree_path, dependency_bin)
            for command in verification_commands
            if command
        ]
        usage = self._usage_from_events(codex["events"])
        return {
            "branch_name": branch_name,
            "worktree_path": worktree_path,
            "codex": codex,
            "verification": verification,
            "usage": usage,
            "diff": RepositoryAdapter(worktree_path).get_diff(),
        }

    def _run_verification(
        self,
        command: str,
        worktree_path: str,
        dependency_bin: Path,
    ) -> dict[str, Any]:
        original = self._run(
            shlex.split(command),
            worktree_path,
            600,
            path_prefix=dependency_bin,
        )
        reason = resource_failure_reason(original)
        retry_command = sequential_retry_command(command) if reason else None
        if not retry_command:
            return {
                "command": command,
                **original,
                **(
                    {
                        "failure_classification": "ENVIRONMENT",
                        "failure_reason": "INFRASTRUCTURE_RESOURCE",
                    }
                    if reason
                    else {}
                ),
            }

        retry = self._run(
            shlex.split(retry_command),
            worktree_path,
            600,
            path_prefix=dependency_bin,
        )
        retry_resource_reason = resource_failure_reason(retry)
        return {
            "command": retry_command,
            **retry,
            **(
                {
                    "failure_classification": "ENVIRONMENT",
                    "failure_reason": "INFRASTRUCTURE_RESOURCE",
                }
                if retry_resource_reason
                else {}
            ),
            "retry_evidence": {
                "reason": reason,
                "classification": "ENVIRONMENT",
                "category": "INFRASTRUCTURE_RESOURCE",
                "original": {"command": command, **original},
                "retry": {"command": retry_command, **retry},
            },
        }

    @staticmethod
    def _branch_name(task_id: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9-]", "-", task_id).strip("-")
        if not normalized:
            raise ValueError("task_id must contain at least one alphanumeric character.")
        return f"sacm/{normalized[:48]}"

    @staticmethod
    def _run(
        command: list[str],
        cwd: str,
        timeout: int,
        *,
        path_prefix: Path | None = None,
    ) -> dict[str, Any]:
        started_at = time.monotonic()
        env = None
        if path_prefix is not None:
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join(
                [str(path_prefix), env.get("PATH", "")]
            ).rstrip(os.pathsep)
        try:
            completed = subprocess.run(
                command,
                cwd=Path(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env,
            )
        except FileNotFoundError:
            return {
                "returncode": 127,
                "stdout": "",
                "stderr": f"Command not found: {command[0]}",
                "events": [],
                "duration_ms": int((time.monotonic() - started_at) * 1_000),
            }
        except subprocess.TimeoutExpired:
            return {
                "returncode": 124,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds.",
                "events": [],
                "duration_ms": int((time.monotonic() - started_at) * 1_000),
            }

        full_stdout = completed.stdout
        stdout = full_stdout[-20_000:]
        return {
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": completed.stderr[-8_000:],
            "events": CodexExecutorAdapter._json_events(full_stdout),
            "duration_ms": int((time.monotonic() - started_at) * 1_000),
        }

    @staticmethod
    def _json_events(output: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    @staticmethod
    def _usage_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        usage_records: list[dict[str, Any]] = []
        for event in events:
            data = event.get("data")
            if not isinstance(data, dict):
                data = {}
            payload = event.get("usage") or data.get("usage")
            if not isinstance(payload, dict):
                continue
            input_tokens = payload.get("input_tokens", payload.get("prompt_tokens"))
            output_tokens = payload.get("output_tokens", payload.get("completion_tokens", 0))
            if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
                continue
            model = event.get("model") or data.get("model") or "unknown"
            usage_records.append(
                {
                    "provider": "codex",
                    "model": str(model),
                    "operation": "code_execution",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
            )
        return usage_records
