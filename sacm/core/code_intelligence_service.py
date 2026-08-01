import hashlib
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

MAX_FINGERPRINT_FILES = 100_000
MAX_INDEXER_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_INDEXER_DIAGNOSTIC_BYTES = 64 * 1024
DEFAULT_SCIP_METADATA_PATH = ".sacm/index.scip.meta.json"
EXCLUDED_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".sacm",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "bin",
    "build",
    "coverage",
    "dist",
    "generated",
    "node_modules",
    "obj",
    "out",
    "target",
    "vendor",
    "venv",
}
INDEXABLE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".swift",
    ".ts",
    ".tsx",
}
INDEXER_CONFIG_NAMES = {
    "Cargo.lock",
    "Cargo.toml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "go.mod",
    "go.sum",
    "jsconfig.json",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pom.xml",
    "pyproject.toml",
    "settings.gradle",
    "settings.gradle.kts",
    "tsconfig.json",
    "yarn.lock",
}


@dataclass(frozen=True)
class RepositoryCodeState:
    revision: str
    workspace_hash: str
    dirty: bool
    fingerprint_complete: bool


class ScipIndexingService:
    """Runs administrator-configured SCIP indexers without a shell."""

    def __init__(self) -> None:
        self.enabled = os.getenv("SACM_SCIP_AUTO_INDEX", "").lower() in {
            "1",
            "true",
            "yes",
        }

    def ensure_index(
        self,
        repository_root: Path,
        state: RepositoryCodeState,
        source_paths: list[str],
        *,
        index_relative_path: str,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        registry = self._registry()
        selected = self._select_indexers(registry, source_paths)
        if not selected:
            return {
                "status": "UNAVAILABLE",
                "reason": "no_configured_indexer",
            }
        index_path = self._safe_path(repository_root, index_relative_path)
        metadata_path = self._safe_path(
            repository_root,
            os.getenv("SACM_SCIP_METADATA_PATH")
            or DEFAULT_SCIP_METADATA_PATH,
        )
        expected_indexers = [
            {
                "language": language,
                "name": str(config.get("name") or language),
                "version": str(config.get("version") or "unknown"),
            }
            for language, config in selected
        ]
        current = self._current_snapshot(
            index_path,
            metadata_path,
            state,
            expected_indexers,
        )
        if current is not None:
            return current
        documents: list[dict[str, Any]] = []
        external_symbols: list[dict[str, Any]] = []
        indexers: list[dict[str, str]] = []
        with tempfile.TemporaryDirectory(prefix="sacm-scip-") as directory:
            temporary = Path(directory)
            for language, config in selected:
                index_output = temporary / f"{language}.scip"
                values = {
                    "index": str(index_output),
                    "project_name": repository_root.name,
                    "repository": str(repository_root),
                    "revision": state.revision,
                }
                self._run(
                    self._render(config["index_command"], values),
                    cwd=repository_root,
                    capture_limit=MAX_INDEXER_DIAGNOSTIC_BYTES,
                )
                raw_json = self._run(
                    self._render(config["print_command"], values),
                    cwd=repository_root,
                    capture_limit=MAX_INDEXER_OUTPUT_BYTES,
                )
                payload = json.loads(raw_json)
                if not isinstance(payload, dict):
                    raise ValueError("SCIP print output must be a JSON object.")
                language_documents = payload.get("documents") or []
                language_external = payload.get(
                    "external_symbols",
                    payload.get("externalSymbols", []),
                ) or []
                if not isinstance(language_documents, list) or not isinstance(
                    language_external, list
                ):
                    raise ValueError("SCIP print output has invalid collections.")
                for document in language_documents:
                    if isinstance(document, dict):
                        document["sacm_indexer"] = language
                documents.extend(language_documents)
                external_symbols.extend(language_external)
                indexers.append(
                    {
                        "language": language,
                        "name": str(config.get("name") or language),
                        "version": str(config.get("version") or "unknown"),
                    }
                )
        combined = {
            "metadata": {
                "tool_info": {
                    "name": "sacm-auto-scip",
                    "version": "1",
                    "arguments": [item["name"] for item in indexers],
                },
                "project_root": repository_root.as_uri(),
            },
            "documents": documents,
            "external_symbols": external_symbols,
        }
        encoded = json.dumps(
            combined, sort_keys=True, separators=(",", ":")
        ).encode()
        if len(encoded) > MAX_INDEXER_OUTPUT_BYTES:
            raise ValueError("Combined SCIP JSON exceeds the configured limit.")
        manifest = {
            "schema_version": "code-intelligence-snapshot/v1",
            "repository_revision": state.revision,
            "workspace_hash": state.workspace_hash,
            "workspace_complete": state.fingerprint_complete,
            "index_sha256": hashlib.sha256(encoded).hexdigest(),
            "generated_at": datetime.now(UTC).isoformat(),
            "indexers": indexers,
        }
        index_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(index_path, encoded)
        self._atomic_write(
            metadata_path,
            json.dumps(manifest, sort_keys=True).encode(),
        )
        return manifest

    @staticmethod
    def _registry() -> dict[str, dict[str, Any]]:
        raw = os.getenv("SACM_SCIP_INDEXERS_JSON", "{}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("SACM_SCIP_INDEXERS_JSON is invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("SACM_SCIP_INDEXERS_JSON must be an object.")
        result: dict[str, dict[str, Any]] = {}
        for language, config in payload.items():
            if not isinstance(language, str) or not isinstance(config, dict):
                continue
            extensions = config.get("extensions")
            index_command = config.get("index_command")
            print_command = config.get("print_command")
            if (
                isinstance(extensions, list)
                and extensions
                and all(isinstance(item, str) for item in extensions)
                and isinstance(index_command, list)
                and index_command
                and all(isinstance(item, str) for item in index_command)
                and isinstance(print_command, list)
                and print_command
                and all(isinstance(item, str) for item in print_command)
            ):
                result[language] = config
        return result

    @staticmethod
    def _select_indexers(
        registry: dict[str, dict[str, Any]], paths: list[str]
    ) -> list[tuple[str, dict[str, Any]]]:
        selected: list[tuple[str, dict[str, Any]]] = []
        suffixes = {Path(path).suffix.lower() for path in paths}
        for language, config in sorted(registry.items()):
            extensions = {str(item).lower() for item in config["extensions"]}
            if suffixes & extensions:
                selected.append((language, config))
        return selected

    @staticmethod
    def _render(command: list[str], values: dict[str, str]) -> list[str]:
        rendered = [item.format_map(values) for item in command]
        if any("\x00" in item or "\n" in item or "\r" in item for item in rendered):
            raise ValueError("SCIP indexer command contains invalid characters.")
        return rendered

    @staticmethod
    def _run(
        command: list[str],
        *,
        cwd: Path,
        capture_limit: int,
        timeout_seconds: int | None = None,
    ) -> bytes:
        timeout = timeout_seconds or int(
            os.getenv("SACM_SCIP_INDEX_TIMEOUT_SECONDS", "600")
        )
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env={
                key: value
                for key, value in os.environ.items()
                if key in {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"}
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            ScipIndexingService._terminate(process)
            raise RuntimeError("SCIP indexer pipes are unavailable.")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        output = bytearray()
        diagnostic = bytearray()
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                events = selector.select(min(remaining, 0.5))
                for key, _ in events:
                    chunk = os.read(key.fd, 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = output if key.data == "stdout" else diagnostic
                    target.extend(chunk)
                    limit = (
                        capture_limit
                        if key.data == "stdout"
                        else MAX_INDEXER_DIAGNOSTIC_BYTES
                    )
                    if len(target) > limit:
                        raise ValueError(
                            "SCIP indexer output exceeded the configured limit."
                        )
            returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except (ValueError, subprocess.TimeoutExpired):
            ScipIndexingService._terminate(process)
            raise
        finally:
            selector.close()
        if returncode != 0:
            message = diagnostic.decode(errors="replace")[-2_000:]
            raise RuntimeError(
                f"SCIP indexer exited with {returncode}: {message}"
            )
        return bytes(output)

    @staticmethod
    def _safe_path(root: Path, relative: str) -> Path:
        value = PurePosixPath(relative)
        if value.is_absolute() or ".." in relative.split("/"):
            raise ValueError("SCIP output paths must be repository-relative.")
        resolved = (root / value.as_posix()).resolve()
        if root not in resolved.parents:
            raise ValueError("SCIP output path escapes the repository.")
        return resolved

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        replaced = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
            replaced = True
        finally:
            if not replaced:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (PermissionError, ProcessLookupError):
            pass
        time.sleep(0.1)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    @staticmethod
    def _current_snapshot(
        index_path: Path,
        metadata_path: Path,
        state: RepositoryCodeState,
        indexers: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        if not index_path.is_file() or not metadata_path.is_file():
            return None
        try:
            if (
                metadata_path.stat().st_size > 64 * 1024
                or index_path.stat().st_size > MAX_INDEXER_OUTPUT_BYTES
            ):
                return None
            manifest = json.loads(metadata_path.read_bytes())
            if not isinstance(manifest, dict):
                return None
            if (
                manifest.get("repository_revision") != state.revision
                or manifest.get("workspace_hash") != state.workspace_hash
                or not state.fingerprint_complete
                or manifest.get("workspace_complete") is not True
                or manifest.get("indexers") != indexers
                or manifest.get("index_sha256")
                != hashlib.sha256(index_path.read_bytes()).hexdigest()
            ):
                return None
            return manifest
        except (OSError, ValueError, RecursionError):
            return None


def inspect_repository_state(root: Path) -> RepositoryCodeState:
    revision = _git_revision(root)
    dirty = _git_dirty(root) if revision else True
    if revision and not dirty:
        workspace_hash = revision
        fingerprint_complete = True
    else:
        workspace_hash, fingerprint_complete = _workspace_fingerprint(root)
    return RepositoryCodeState(
        revision=revision or f"workspace:{workspace_hash}",
        workspace_hash=workspace_hash,
        dirty=dirty,
        fingerprint_complete=fingerprint_complete,
    )


def workspace_fingerprint(root: Path) -> str:
    return _workspace_fingerprint(root)[0]


def _workspace_fingerprint(root: Path) -> tuple[str, bool]:
    digest = hashlib.sha256()
    count = 0
    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(
            item
            for item in directories
            if item not in EXCLUDED_DIRECTORIES
            and not item.startswith(".")
            and not (Path(current) / item).is_symlink()
        )
        for filename in sorted(filenames):
            if count >= MAX_FINGERPRINT_FILES:
                digest.update(b"TRUNCATED")
                return digest.hexdigest(), False
            path = Path(current) / filename
            if path.is_symlink():
                continue
            try:
                resolved = path.resolve()
                if root not in resolved.parents or not resolved.is_file():
                    continue
                size = resolved.stat().st_size
                relative = resolved.relative_to(root).as_posix()
                digest.update(relative.encode())
                digest.update(str(size).encode())
                if _index_affecting(relative):
                    with resolved.open("rb") as handle:
                        file_digest = hashlib.sha256()
                        while chunk := handle.read(1024 * 1024):
                            file_digest.update(chunk)
                    digest.update(file_digest.digest())
            except OSError:
                continue
            count += 1
    return digest.hexdigest(), True


def _index_affecting(relative_path: str) -> bool:
    path = Path(relative_path)
    name = path.name
    return bool(
        path.suffix.lower() in INDEXABLE_SUFFIXES
        or name in INDEXER_CONFIG_NAMES
        or name.startswith("requirements")
        or path.suffix.lower() in {".csproj", ".fsproj", ".sln"}
    )


def _git_revision(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or len(completed.stdout) > 100:
        return None
    value = completed.stdout.decode(errors="replace").strip()
    return value if len(value) == 40 else None


def _git_dirty(root: Path) -> bool:
    try:
        output = ScipIndexingService._run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
                "--",
                ".",
                ":(exclude).sacm",
            ],
            cwd=root,
            capture_limit=64 * 1024,
            timeout_seconds=5,
        )
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        return True
    return bool(output.strip())
