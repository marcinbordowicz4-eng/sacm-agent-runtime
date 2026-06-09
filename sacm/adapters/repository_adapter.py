import os
import subprocess
from pathlib import Path


class RepositoryAdapter:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

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
        worktree_root = self.repo_path.parent / f"{self.repo_path.name}-sacm-worktrees"
        worktree_path = worktree_root / branch_name
        worktree_root.mkdir(exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
        return str(worktree_path)

    def get_diff(self) -> str:
        result = subprocess.run(
            ["git", "diff"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout

    def apply_patch(self, patch: str) -> None:
        proc = subprocess.run(
            ["git", "apply", "-"],
            input=patch,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to apply patch: {proc.stderr.strip()}")

    def run_command(self, command: str) -> dict:
        result = subprocess.run(
            command,
            shell=True,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:2000],
        }
