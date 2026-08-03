import json
import os
import re
import selectors
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from sacm.adapters.repository_adapter import DependencyCache, RepositoryAdapter
from sacm.core.verification_execution import (
    resource_failure_reason,
    sequential_retry_command,
)


class CodexExecutorAdapter:
    """Runs Codex in an isolated Git worktree and returns execution evidence."""

    _DEPENDENCY_MARKER = ".sacm-dependency-cache-key"

    def __init__(self, repo_path: str) -> None:
        self.repository = RepositoryAdapter(repo_path)

    def execute(
        self,
        task_id: str,
        prompt: str,
        verification_commands: list[str],
        *,
        telemetry_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        executor = os.getenv("SACM_CODE_EXECUTOR", "codex")
        provider = "copilot" if executor == "copilot" else "codex"
        branch_name = self._branch_name(task_id)
        worktree_path = self.repository.create_worktree(branch_name)
        dependency_cache = self.repository.dependency_cache(worktree_path)
        dependency_environment = (
            dependency_cache.environment if dependency_cache else None
        )
        dependency_setup, dependency_cache_status = self._prepare_dependencies(
            worktree_path,
            dependency_cache,
            telemetry_sink=telemetry_sink,
        )
        if (
            dependency_cache is not None
            and dependency_setup
            and dependency_setup["returncode"] != 0
        ):
            return {
                "executor": executor,
                "provider": provider,
                "branch_name": branch_name,
                "worktree_path": worktree_path,
                "codex": dependency_setup,
                "verification": [],
                "usage": [],
                "diff": RepositoryAdapter(worktree_path).get_diff(),
                "dependency_cache": {
                    "manager": dependency_cache.manager,
                    "cache_key": dependency_cache.cache_key,
                    "prepared": False,
                    "status": dependency_cache_status,
                },
                "dependency_setup": dependency_setup,
            }
        if dependency_cache and dependency_cache_status in {"installed", "shared"}:
            self._write_dependency_marker(worktree_path, dependency_cache.cache_key)
        command = (
            [
                "copilot",
                "--prompt",
                prompt,
                "--allow-all",
                "--no-ask-user",
                "--no-auto-update",
                "--no-remote",
                "--output-format",
                "json",
            ]
            if executor == "copilot"
            else ["codex", "exec", "--full-auto", "--json", prompt]
        )
        codex = self._run_with_telemetry(
            command,
            worktree_path,
            timeout=1_800,
            tool=executor,
            telemetry_sink=telemetry_sink,
            environment=dependency_environment,
            provider=provider,
        )
        dependency_bin = Path(worktree_path) / "node_modules" / ".bin"
        verification = [
            self._run_verification(
                command,
                worktree_path,
                dependency_bin,
                environment=dependency_environment,
                telemetry_sink=telemetry_sink,
            )
            for command in verification_commands
            if command
        ]
        usage = self._usage_from_events(codex["events"], provider=provider)
        return {
            "executor": executor,
            "provider": provider,
            "branch_name": branch_name,
            "worktree_path": worktree_path,
            "codex": codex,
            "verification": verification,
            "usage": usage,
            "diff": RepositoryAdapter(worktree_path).get_diff(),
            "dependency_cache": (
                {
                    "manager": dependency_cache.manager,
                    "cache_key": dependency_cache.cache_key,
                    "prepared": True,
                    "status": dependency_cache_status,
                }
                if dependency_cache
                else None
            ),
            "dependency_setup": dependency_setup,
        }

    def _prepare_dependencies(
        self,
        worktree_path: str,
        dependency_cache: DependencyCache | None,
        *,
        telemetry_sink: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if dependency_cache is None:
            return None, None
        if self._dependencies_are_ready(worktree_path, dependency_cache.cache_key):
            self._emit_dependency_cache_telemetry(
                telemetry_sink, dependency_cache, "worktree"
            )
            return None, "worktree"
        if dependency_cache.manager == "npm":
            with self.repository.node_dependency_cache_lock(dependency_cache):
                if self.repository.restore_node_dependencies(
                    worktree_path, dependency_cache
                ):
                    self._emit_dependency_cache_telemetry(
                        telemetry_sink, dependency_cache, "shared"
                    )
                    return None, "shared"
                self._emit_dependency_cache_telemetry(
                    telemetry_sink, dependency_cache, "miss"
                )
                dependency_setup = self._install_dependencies(
                    worktree_path, dependency_cache, telemetry_sink
                )
                if dependency_setup["returncode"] == 0:
                    self.repository.publish_node_dependencies(
                        worktree_path, dependency_cache
                    )
                    self._emit_dependency_cache_telemetry(
                        telemetry_sink, dependency_cache, "published"
                    )
                return (
                    dependency_setup,
                    "installed"
                    if dependency_setup["returncode"] == 0
                    else "failed",
                )
        self._emit_dependency_cache_telemetry(telemetry_sink, dependency_cache, "miss")
        dependency_setup = self._install_dependencies(
            worktree_path, dependency_cache, telemetry_sink
        )
        return (
            dependency_setup,
            "installed" if dependency_setup["returncode"] == 0 else "failed",
        )

    def _install_dependencies(
        self,
        worktree_path: str,
        dependency_cache: DependencyCache,
        telemetry_sink: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        dependency_setup = self._run_with_telemetry(
            dependency_cache.install_command,
            worktree_path,
            timeout=self._dependency_setup_timeout(),
            tool="dependency_setup",
            telemetry_sink=telemetry_sink,
            environment=dependency_cache.environment,
        )
        dependency_setup["command"] = shlex.join(dependency_cache.install_command)
        return dependency_setup

    def _emit_dependency_cache_telemetry(
        self,
        telemetry_sink: Callable[[dict[str, Any]], None] | None,
        dependency_cache: DependencyCache,
        status: str,
    ) -> None:
        self._emit(
            telemetry_sink,
            {
                "type": "dependency_cache",
                "manager": dependency_cache.manager,
                "cache_key": dependency_cache.cache_key,
                "status": status,
            },
        )

    @classmethod
    def _dependencies_are_ready(cls, worktree_path: str, cache_key: str) -> bool:
        worktree = Path(worktree_path)
        marker = worktree / cls._DEPENDENCY_MARKER
        try:
            return (
                (worktree / "node_modules").is_dir()
                and marker.is_file()
                and marker.read_text(encoding="utf-8").strip() == cache_key
            )
        except OSError:
            return False

    @classmethod
    def _write_dependency_marker(cls, worktree_path: str, cache_key: str) -> None:
        marker = Path(worktree_path) / cls._DEPENDENCY_MARKER
        temporary_marker = marker.with_suffix(f"{marker.suffix}.tmp")
        try:
            temporary_marker.write_text(f"{cache_key}\n", encoding="utf-8")
            temporary_marker.replace(marker)
        except OSError as exc:
            raise RuntimeError(
                f"Could not record prepared dependencies for {worktree_path}: {exc}"
            ) from exc

    def _run_verification(
        self,
        command: str,
        worktree_path: str,
        dependency_bin: Path,
        *,
        environment: dict[str, str] | None = None,
        telemetry_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        original = self._run_with_telemetry(
            shlex.split(command),
            worktree_path,
            600,
            tool="verification",
            telemetry_sink=telemetry_sink,
            path_prefix=dependency_bin,
            environment=environment,
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

        retry = self._run_with_telemetry(
            shlex.split(retry_command),
            worktree_path,
            600,
            tool="verification",
            telemetry_sink=telemetry_sink,
            path_prefix=dependency_bin,
            environment=environment,
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
    def _dependency_setup_timeout() -> int:
        value = os.getenv("SACM_DEPENDENCY_SETUP_TIMEOUT_SECONDS", "3600")
        try:
            timeout = int(value)
        except ValueError as exc:
            raise ValueError(
                "SACM_DEPENDENCY_SETUP_TIMEOUT_SECONDS must be an integer."
            ) from exc
        if timeout <= 0:
            raise ValueError(
                "SACM_DEPENDENCY_SETUP_TIMEOUT_SECONDS must be positive."
            )
        return timeout

    @staticmethod
    def _emit(
        sink: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]
    ) -> None:
        if sink is not None:
            sink(event)

    def _run_with_telemetry(
        self,
        command: list[str],
        cwd: str,
        timeout: int,
        *,
        tool: str,
        telemetry_sink: Callable[[dict[str, Any]], None] | None,
        provider: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        command_text = shlex.join(command)
        self._emit(
            telemetry_sink,
            {"type": "tool_started", "tool": tool, "command": command_text},
        )

        def on_json_event(event: dict[str, Any]) -> None:
            if provider is None:
                return
            for usage in self._usage_from_events([event], provider=provider):
                self._emit(
                    telemetry_sink,
                    {"type": "provider_usage", "usage": usage},
                )

        result = self._run(
            command,
            cwd,
            timeout,
            on_json_event=on_json_event if provider else None,
            **kwargs,
        )
        self._emit(
            telemetry_sink,
            {
                "type": "tool_completed",
                "tool": tool,
                "command": command_text,
                "duration_ms": result["duration_ms"],
                "returncode": result["returncode"],
            },
        )
        return result

    @staticmethod
    def _run(
        command: list[str],
        cwd: str,
        timeout: int,
        *,
        path_prefix: Path | None = None,
        environment: dict[str, str] | None = None,
        on_json_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        started_at = time.monotonic()
        env = None
        if path_prefix is not None or environment:
            env = os.environ.copy()
            if environment:
                env.update(environment)
            if path_prefix is not None:
                env["PATH"] = os.pathsep.join(
                    [str(path_prefix), env.get("PATH", "")]
                ).rstrip(os.pathsep)
        if on_json_event is None:
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
            return {
                "returncode": completed.returncode,
                "stdout": full_stdout[-20_000:],
                "stderr": completed.stderr[-8_000:],
                "events": CodexExecutorAdapter._json_events(full_stdout),
                "duration_ms": int((time.monotonic() - started_at) * 1_000),
            }

        try:
            process = subprocess.Popen(
                command,
                cwd=Path(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError:
            return {
                "returncode": 127,
                "stdout": "",
                "stderr": f"Command not found: {command[0]}",
                "events": [],
                "duration_ms": int((time.monotonic() - started_at) * 1_000),
            }

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        streams = selectors.DefaultSelector()
        assert process.stdout is not None
        assert process.stderr is not None
        streams.register(
            process.stdout,
            selectors.EVENT_READ,
            {"chunks": stdout_chunks, "pending": ""},
        )
        streams.register(
            process.stderr,
            selectors.EVENT_READ,
            {"chunks": stderr_chunks, "pending": ""},
        )
        deadline = started_at + timeout
        timed_out = False
        process_exited_at: float | None = None
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                remaining = 0.1
            if process.poll() is not None:
                process_exited_at = process_exited_at or time.monotonic()
                if time.monotonic() - process_exited_at >= 1.0:
                    break
            for key, _ in streams.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 4_096)
                if not chunk:
                    streams.unregister(key.fileobj)
                    continue
                text = chunk.decode("utf-8", errors="replace")
                key.data["chunks"].append(text)
                if key.data["chunks"] is stdout_chunks:
                    key.data["pending"] += text
                    lines = key.data["pending"].splitlines(keepends=True)
                    key.data["pending"] = ""
                    if lines and not lines[-1].endswith(("\n", "\r")):
                        key.data["pending"] = lines.pop()
                    for line in lines:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event, dict):
                            on_json_event(event)
        for key in list(streams.get_map().values()):
            streams.unregister(key.fileobj)
            key.fileobj.close()
        process.wait()
        full_stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        if timed_out:
            return {
                "returncode": 124,
                "stdout": full_stdout[-20_000:],
                "stderr": (stderr or f"Command timed out after {timeout} seconds.")[
                    -8_000:
                ],
                "events": CodexExecutorAdapter._json_events(full_stdout),
                "duration_ms": int((time.monotonic() - started_at) * 1_000),
            }
        return {
            "returncode": process.returncode,
            "stdout": full_stdout[-20_000:],
            "stderr": stderr[-8_000:],
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
    def _usage_from_events(
        events: list[dict[str, Any]],
        *,
        provider: str = "codex",
    ) -> list[dict[str, Any]]:
        usage_records: list[dict[str, Any]] = []
        for event in events:
            data = event.get("data")
            if not isinstance(data, dict):
                data = {}
            payload = event.get("usage") or data.get("usage")
            if isinstance(payload, dict):
                input_tokens = payload.get(
                    "input_tokens", payload.get("prompt_tokens")
                )
                output_tokens = payload.get(
                    "output_tokens", payload.get("completion_tokens", 0)
                )
                if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                    model = event.get("model") or data.get("model") or "unknown"
                    usage_records.append(
                        {
                            "provider": provider,
                            "model": str(model),
                            "operation": "code_execution",
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        }
                    )
                continue

            # Copilot CLI JSONL reports completion tokens on assistant messages
            # and subscription usage in a separate session checkpoint.
            if event.get("type") == "assistant.message":
                output_tokens = data.get("outputTokens")
                if isinstance(output_tokens, int):
                    usage_records.append(
                        {
                            "provider": provider,
                            "model": str(data.get("model") or "unknown"),
                            "operation": "code_execution",
                            "input_tokens": 0,
                            "output_tokens": output_tokens,
                        }
                    )
            elif event.get("type") == "session.usage_checkpoint":
                premium_requests = data.get("totalPremiumRequests")
                total_nano_aiu = data.get("totalNanoAiu")
                if isinstance(premium_requests, int) or isinstance(
                    total_nano_aiu, int
                ):
                    usage_records.append(
                        {
                            "provider": provider,
                            "model": "subscription",
                            "operation": "code_execution",
                            "input_tokens": 0,
                            "output_tokens": 0,
                            **(
                                {"premium_requests": premium_requests}
                                if isinstance(premium_requests, int)
                                else {}
                            ),
                            **(
                                {"total_nano_aiu": total_nano_aiu}
                                if isinstance(total_nano_aiu, int)
                                else {}
                            ),
                        }
                    )
        return usage_records
