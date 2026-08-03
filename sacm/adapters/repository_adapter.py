import fnmatch
import hashlib
import os
import re
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DependencyCache:
    manager: str
    cache_key: str
    cache_path: Path
    environment: dict[str, str]
    install_command: list[str]


@dataclass(frozen=True)
class PatchFileState:
    path: Path
    content: bytes | None
    mode: int | None


class RepositoryError(Exception):
    """Base error for actionable repository failures."""


class RepositoryPathError(RepositoryError, ValueError):
    pass


class RepositoryOperationError(RepositoryError, RuntimeError):
    pass


class RepositoryAdapter:
    def __init__(self, repo_path: str):
        self.requested_repo_path = Path(repo_path).expanduser().resolve()
        self.repo_path = self._translate_host_path(self.requested_repo_path)
        repository_root = os.getenv("SACM_REPOSITORY_ROOT")
        if repository_root:
            allowed_roots = [Path(repository_root).resolve()]
            worktree_root = os.getenv("SACM_WORKTREE_ROOT")
            if worktree_root:
                allowed_roots.append(Path(worktree_root).resolve())
            if not any(
                self.repo_path == allowed_root or allowed_root in self.repo_path.parents
                for allowed_root in allowed_roots
            ):
                raise RepositoryPathError(
                    f"Repository path {self.requested_repo_path} resolves to "
                    f"{self.repo_path}, outside SACM_REPOSITORY_ROOT and "
                    "SACM_WORKTREE_ROOT: "
                    + ", ".join(str(root) for root in allowed_roots)
                )
        if not self.repo_path.is_dir():
            raise RepositoryPathError(
                f"Repository directory does not exist in the runtime: {self.repo_path}"
            )

    @staticmethod
    def _translate_host_path(path: Path) -> Path:
        host_root = os.getenv("SACM_HOST_REPOSITORY_ROOT")
        container_root = os.getenv("SACM_REPOSITORY_ROOT")
        if not host_root or not container_root:
            return path
        host = Path(host_root).expanduser().resolve()
        if path != host and host not in path.parents:
            return path
        return (Path(container_root).resolve() / path.relative_to(host)).resolve()

    def list_files(self) -> list[str]:
        result: list[str] = []
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [
                directory
                for directory in dirs
                if not directory.startswith(".")
                and directory not in {"node_modules", "target", "__pycache__"}
            ]
            for filename in files:
                full = Path(root) / filename
                result.append(str(full.relative_to(self.repo_path)))
        return sorted(result)

    def read_file(self, path: str) -> str:
        return (self.repo_path / path).read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        target = self.repo_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def create_worktree(self, branch_name: str) -> str:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch_name)
            or ".." in branch_name
            or branch_name.startswith("-")
        ):
            raise RepositoryPathError(f"Invalid worktree branch name: {branch_name!r}")

        configured_root = os.getenv("SACM_WORKTREE_ROOT")
        worktree_root = (
            Path(configured_root).resolve()
            if configured_root
            else (self.repo_path.parent / f"{self.repo_path.name}-sacm-worktrees").resolve()
        )
        worktree_path = (worktree_root / branch_name).resolve()
        if worktree_root not in worktree_path.parents:
            raise RepositoryPathError(
                "Worktree path must remain inside the SACM worktree root."
            )

        self._git(["worktree", "prune"], check=True)
        if worktree_path.exists():
            if (
                worktree_path.is_dir()
                and self._worktree_branch(worktree_path) == branch_name
            ):
                self._ensure_independent_node_modules(worktree_path)
                return str(worktree_path)
            raise RepositoryOperationError(
                f"Worktree path {worktree_path} already exists but is not attached "
                f"to branch {branch_name}."
            )

        try:
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RepositoryOperationError(
                f"Cannot create worktree directory {worktree_path.parent}: {exc}"
            ) from exc

        branch_exists = (
            self._git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                check=False,
            ).returncode
            == 0
        )
        arguments = ["worktree", "add"]
        if branch_exists:
            arguments.extend([str(worktree_path), branch_name])
        else:
            arguments.extend(["-b", branch_name, str(worktree_path)])
        try:
            self._git(arguments, check=True)
        except RepositoryOperationError:
            if (
                worktree_path.is_dir()
                and self._worktree_branch(worktree_path) == branch_name
            ):
                self._ensure_independent_node_modules(worktree_path)
                return str(worktree_path)
            raise
        self._ensure_independent_node_modules(worktree_path)
        return str(worktree_path)

    def _ensure_independent_node_modules(self, worktree_path: Path) -> None:
        target = worktree_path / "node_modules"
        if target.is_symlink():
            try:
                target.unlink()
            except OSError as exc:
                raise RepositoryOperationError(
                    f"Cannot remove unsafe shared dependency link {target}: {exc}"
                ) from exc

    def dependency_cache(self, worktree_path: str | Path) -> DependencyCache | None:
        worktree = Path(worktree_path).resolve()
        lockfile = self._dependency_lockfile(worktree)
        if lockfile is None:
            return None
        manager, path = lockfile
        digest = hashlib.sha256()
        digest.update(f"{manager}\0{path.name}\0".encode())
        digest.update(path.read_bytes())
        cache_key = f"{manager}-{digest.hexdigest()[:24]}"
        configured_root = os.getenv("SACM_DEPENDENCY_CACHE_ROOT")
        if configured_root:
            root = Path(configured_root).expanduser().resolve()
        else:
            worktree_root = os.getenv("SACM_WORKTREE_ROOT")
            root = (
                Path(worktree_root).expanduser().resolve()
                if worktree_root
                else (
                    self.repo_path.parent
                    / f".{self.repo_path.name}-sacm-dependency-cache"
                ).resolve()
            )
        cache_path = root / manager / cache_key
        try:
            cache_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RepositoryOperationError(
                f"Cannot prepare dependency cache {cache_path}: {exc}"
            ) from exc
        resolved_cache = cache_path.resolve()
        if root != resolved_cache and root not in resolved_cache.parents:
            raise RepositoryOperationError(
                "Dependency cache path must remain inside SACM_DEPENDENCY_CACHE_ROOT."
            )
        environment = {
            "npm": {"npm_config_cache": str(resolved_cache)},
            "pnpm": {"pnpm_config_store_dir": str(resolved_cache)},
            "yarn": {"YARN_CACHE_FOLDER": str(resolved_cache)},
            "bun": {"BUN_INSTALL_CACHE_DIR": str(resolved_cache)},
        }[manager]
        install_command = {
            "npm": ["npm", "ci", "--prefer-offline", "--no-audit", "--no-fund"],
            "pnpm": ["pnpm", "install", "--frozen-lockfile", "--prefer-offline"],
            "yarn": ["yarn", "install", "--immutable"],
            "bun": ["bun", "install", "--frozen-lockfile"],
        }[manager]
        return DependencyCache(
            manager=manager,
            cache_key=cache_key,
            cache_path=resolved_cache,
            environment=environment,
            install_command=install_command,
        )

    @staticmethod
    def _dependency_lockfile(worktree_path: Path) -> tuple[str, Path] | None:
        candidates = (
            ("npm", "npm-shrinkwrap.json"),
            ("npm", "package-lock.json"),
            ("pnpm", "pnpm-lock.yaml"),
            ("yarn", "yarn.lock"),
            ("bun", "bun.lock"),
            ("bun", "bun.lockb"),
        )
        for manager, filename in candidates:
            path = worktree_path / filename
            if path.is_file():
                return manager, path
        return None

    def _worktree_branch(self, worktree_path: Path) -> str | None:
        result = self._git(
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=worktree_path,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _git(
        self,
        arguments: list[str],
        *,
        cwd: Path | None = None,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", *arguments],
                cwd=cwd or self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RepositoryOperationError(
                "Git executable is unavailable in the SACM runtime image."
            ) from exc
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
            raise RepositoryOperationError(
                f"Git command failed (`git {' '.join(arguments)}`): {message}"
            )
        return result

    def get_diff(self) -> str:
        result = self._git(["diff"], check=False)
        return result.stdout

    def apply_patch(self, patch: str) -> dict[str, object]:
        paths = self._validate_patch(patch)
        states = self._snapshot_patch_files(paths)
        check = self._run_git_apply(
            patch,
            ["apply", "--check", "--whitespace=error-all", "-"],
        )
        if check.returncode != 0:
            raise RepositoryOperationError(
                f"Patch preflight failed: {check.stderr.strip()}"
            )
        proc = self._run_git_apply(patch, ["apply", "--whitespace=error-all", "-"])
        if proc.returncode == 0:
            return {
                "status": "applied",
                "changed_files": sorted(paths),
                "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            }
        rollback_error = self._restore_patch_files(states)
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown Git error"
        if rollback_error:
            detail += f"; rollback failed: {rollback_error}"
        raise RepositoryOperationError(f"Failed to apply patch: {detail}")

    def _run_git_apply(
        self,
        patch: str,
        arguments: list[str],
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *arguments],
                input=patch,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RepositoryOperationError(
                "Git executable is unavailable in the SACM runtime image."
            ) from exc

    def _validate_patch(self, patch: str) -> set[str]:
        encoded = patch.encode("utf-8")
        max_bytes = int(os.getenv("SACM_PATCH_MAX_BYTES", "1048576"))
        max_files = int(os.getenv("SACM_PATCH_MAX_FILES", "100"))
        if not patch.strip():
            raise RepositoryOperationError("Patch must not be empty.")
        if len(encoded) > max_bytes:
            raise RepositoryOperationError(
                f"Patch exceeds SACM_PATCH_MAX_BYTES ({max_bytes})."
            )
        if "\x00" in patch:
            raise RepositoryOperationError("Patch must not contain NUL bytes.")
        if re.search(r"^(?:new file mode|old mode) 120000$", patch, re.MULTILINE):
            raise RepositoryOperationError("Patches may not create or modify symlinks.")

        paths: set[str] = set()
        for match in re.finditer(
            r"^diff --git a/(.+?) b/(.+?)$",
            patch,
            re.MULTILINE,
        ):
            paths.update(match.groups())
        if not paths:
            raise RepositoryOperationError(
                "Patch must contain at least one standard `diff --git` header."
            )
        if len(paths) > max_files:
            raise RepositoryOperationError(
                f"Patch exceeds SACM_PATCH_MAX_FILES ({max_files})."
            )
        for path in paths:
            self._validate_patch_path(path)
        return paths

    def _validate_patch_path(self, path: str) -> None:
        candidate = Path(path)
        if (
            not path
            or candidate.is_absolute()
            or ".." in candidate.parts
            or ".git" in candidate.parts
        ):
            raise RepositoryPathError(f"Unsafe patch path: {path!r}")
        resolved = (self.repo_path / candidate).resolve()
        if self.repo_path != resolved and self.repo_path not in resolved.parents:
            raise RepositoryPathError(f"Patch path escapes repository: {path!r}")
        denied = [
            item.strip()
            for item in os.getenv(
                "SACM_PATCH_DENIED_GLOBS",
                ".env,.env.*,*.pem,*.key,*.p12,*.pfx,*.mobileprovision,"
                "**/id_rsa*,**/generated/*.pdf",
            ).split(",")
            if item.strip()
        ]
        if any(fnmatch.fnmatch(path, pattern) for pattern in denied):
            raise RepositoryPathError(f"Patch path is protected by policy: {path!r}")
        if resolved.is_symlink():
            raise RepositoryPathError(
                f"Patch may not modify an existing symlink: {path!r}"
            )

    def _snapshot_patch_files(self, paths: set[str]) -> list[PatchFileState]:
        states: list[PatchFileState] = []
        for path in sorted(paths):
            target = self.repo_path / path
            if target.exists():
                if not target.is_file():
                    raise RepositoryPathError(
                        f"Patch target must be a regular file: {path!r}"
                    )
                states.append(
                    PatchFileState(
                        path=target,
                        content=target.read_bytes(),
                        mode=stat.S_IMODE(target.stat().st_mode),
                    )
                )
            else:
                states.append(PatchFileState(path=target, content=None, mode=None))
        return states

    @staticmethod
    def _restore_patch_files(states: list[PatchFileState]) -> str | None:
        errors: list[str] = []
        for state in states:
            try:
                if state.content is None:
                    if state.path.exists() or state.path.is_symlink():
                        state.path.unlink()
                    continue
                state.path.parent.mkdir(parents=True, exist_ok=True)
                state.path.write_bytes(state.content)
                if state.mode is not None:
                    state.path.chmod(state.mode)
            except OSError as exc:
                errors.append(f"{state.path}: {exc}")
        return "; ".join(errors) or None

    def run_command(self, command: str) -> dict:
        arguments = shlex.split(command)
        if not arguments:
            raise ValueError("Command must not be empty.")
        try:
            result = subprocess.run(
                arguments,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RepositoryOperationError(
                f"Command executable is unavailable: {arguments[0]}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RepositoryOperationError(
                f"Command timed out after 120 seconds: {command}"
            ) from exc
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:2000],
        }
