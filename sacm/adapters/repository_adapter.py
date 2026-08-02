import os
import re
import shlex
import subprocess
from pathlib import Path


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
                self._reuse_node_modules(worktree_path)
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
                self._reuse_node_modules(worktree_path)
                return str(worktree_path)
            raise
        self._reuse_node_modules(worktree_path)
        return str(worktree_path)

    def _reuse_node_modules(self, worktree_path: Path) -> None:
        source = self.repo_path / "node_modules"
        target = worktree_path / "node_modules"
        if not source.exists() or not source.is_dir():
            return
        if target.exists() or target.is_symlink():
            return
        try:
            target.symlink_to(source.resolve(), target_is_directory=True)
        except OSError as exc:
            raise RepositoryOperationError(
                f"Cannot reuse repository dependencies in {worktree_path}: {exc}"
            ) from exc

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

    def apply_patch(self, patch: str) -> None:
        try:
            proc = subprocess.run(
                ["git", "apply", "-"],
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
        if proc.returncode != 0:
            raise RepositoryOperationError(
                f"Failed to apply patch: {proc.stderr.strip()}"
            )

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
