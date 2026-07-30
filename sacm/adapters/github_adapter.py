import json
import subprocess
from pathlib import Path
from typing import Any


class GitHubAdapter:
    """GitHub CLI bridge for repository-scoped delivery operations."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path).resolve()

    def create_issue(self, title: str, body: str) -> dict[str, Any]:
        return self._gh(["issue", "create", "--title", title, "--body", body])

    def push_branch(self, branch_name: str) -> dict[str, Any]:
        self._require_sacm_branch(branch_name)
        return self._git(["push", "--set-upstream", "origin", branch_name])

    def commit_push_and_open_pull_request(
        self, title: str, body: str, branch_name: str, base: str, *, draft: bool = False
    ) -> dict[str, Any]:
        status = self._git(["status", "--porcelain"])
        if status["returncode"] != 0:
            return status
        if not status["stdout"].strip():
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "No changes are available to commit.",
            }

        for command in (
            ["add", "--all"],
            ["commit", "--message", title],
            ["push", "--set-upstream", "origin", branch_name],
        ):
            result = self._git(command)
            if result["returncode"] != 0:
                return result
        return self.open_pull_request(title, body, branch_name, base, draft=draft)

    def auth_status(self) -> dict[str, Any]:
        return self._gh(["auth", "status"])

    def open_pull_request(
        self, title: str, body: str, head: str, base: str = "main", *, draft: bool = False
    ) -> dict[str, Any]:
        self._require_sacm_branch(head)
        return self._gh(
            [
                "pr",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--head",
                head,
                "--base",
                base,
                *(["--draft"] if draft else []),
            ]
        )

    def read_review_comments(self, pull_request_number: int) -> list[dict[str, Any]]:
        repository = self._repository_name()
        result = self._gh(
            ["api", f"repos/{repository}/pulls/{pull_request_number}/comments"]
        )
        if result["returncode"] != 0:
            raise RuntimeError(result["stderr"])
        payload = json.loads(result["stdout"])
        if not isinstance(payload, list):
            raise RuntimeError("GitHub returned an unexpected review-comment payload.")
        return payload

    def merge_when_green(self, pull_request_number: int) -> dict[str, Any]:
        return {
            "returncode": 126,
            "stdout": "",
            "stderr": (
                "SACM does not merge pull requests. A human must merge through "
                "GitHub after required checks and approvals are satisfied."
            ),
        }

    @staticmethod
    def _require_sacm_branch(branch_name: str) -> None:
        if not branch_name.startswith("sacm/"):
            raise ValueError("SACM may push and open pull requests only from sacm/ branches.")

    def _repository_name(self) -> str:
        result = self._gh(
            ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
        )
        if result["returncode"] != 0:
            raise RuntimeError(result["stderr"])
        return result["stdout"].strip()

    def _gh(self, arguments: list[str]) -> dict[str, Any]:
        return self._run(["gh", *arguments])

    def _git(self, arguments: list[str]) -> dict[str, Any]:
        return self._run(["git", *arguments])

    def _run(self, command: list[str]) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return {
                "returncode": 127,
                "stdout": "",
                "stderr": f"Command not found: {command[0]}",
            }
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8_000:],
            "stderr": completed.stderr[-8_000:],
        }
