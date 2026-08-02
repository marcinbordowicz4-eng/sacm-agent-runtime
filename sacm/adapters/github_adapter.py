import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_SECRET = re.compile(
    r"(?:gh[opsu]_[A-Za-z0-9_]{20,}|https?://[^/\s:@]+:[^@\s/]+@)",
    re.IGNORECASE,
)


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
        self._require_sacm_branch(branch_name)
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

    def publish_draft_pull_request(
        self,
        title: str,
        body: str,
        branch_name: str,
        base: str = "main",
    ) -> dict[str, Any]:
        self._require_sacm_branch(branch_name)
        current_branch = self._git(["branch", "--show-current"])
        if current_branch["returncode"] != 0:
            return self._failed(current_branch)
        if current_branch["stdout"].strip() != branch_name:
            return {
                "status": "failed",
                "error": (
                    f"Delivery worktree is on {current_branch['stdout'].strip()!r}, "
                    f"not expected task branch {branch_name!r}."
                ),
            }

        worktree_status = self._git(["status", "--porcelain"])
        if worktree_status["returncode"] != 0:
            return self._failed(worktree_status)
        dirty = bool(worktree_status["stdout"].strip())
        base_ref = self._base_ref(base)
        if base_ref is None:
            return {
                "status": "failed",
                "error": f"Cannot resolve pull-request base branch {base!r}.",
            }
        ahead = self._git(["rev-list", "--count", f"{base_ref}..HEAD"])
        if ahead["returncode"] != 0:
            return self._failed(ahead)
        try:
            ahead_count = int(ahead["stdout"].strip())
        except ValueError:
            return {
                "status": "failed",
                "error": "Git returned an invalid task-branch commit count.",
            }
        if not dirty and ahead_count == 0:
            return {"status": "skipped", "reason": "no_changes"}

        remote = self._git(["remote", "get-url", "origin"])
        if remote["returncode"] != 0 or not remote["stdout"].strip():
            return {"status": "skipped", "reason": "no_remote"}
        if not self._is_github_remote(remote["stdout"].strip()):
            return {"status": "skipped", "reason": "non_github_remote"}

        if dirty:
            for command in (
                ["add", "--all"],
                ["commit", "--message", title],
            ):
                result = self._git(command)
                if result["returncode"] != 0:
                    return self._failed(result)

        pushed = self.push_branch(branch_name)
        if pushed["returncode"] != 0:
            return self._failed(pushed)

        existing = self._open_pull_requests(branch_name)
        if existing["status"] == "failed":
            return existing
        if existing["pull_requests"]:
            pull_request = existing["pull_requests"][0]
            return {
                "status": "delivered",
                "outcome": "reused",
                "number": pull_request["number"],
                "url": pull_request["url"],
                "draft": bool(pull_request.get("isDraft")),
            }

        created = self.open_pull_request(
            title,
            body,
            branch_name,
            base,
            draft=True,
        )
        if created["returncode"] != 0:
            recovered = self._open_pull_requests(branch_name)
            if recovered["status"] != "failed" and recovered["pull_requests"]:
                pull_request = recovered["pull_requests"][0]
                return {
                    "status": "delivered",
                    "outcome": "reused",
                    "number": pull_request["number"],
                    "url": pull_request["url"],
                    "draft": bool(pull_request.get("isDraft")),
                }
            return self._failed(created)

        refreshed = self._open_pull_requests(branch_name)
        if refreshed["status"] != "failed" and refreshed["pull_requests"]:
            pull_request = refreshed["pull_requests"][0]
            return {
                "status": "delivered",
                "outcome": "created",
                "number": pull_request["number"],
                "url": pull_request["url"],
                "draft": bool(pull_request.get("isDraft", True)),
            }
        url = created["stdout"].strip()
        match = re.search(r"/pull/(\d+)(?:\D|$)", url)
        return {
            "status": "delivered",
            "outcome": "created",
            "number": int(match.group(1)) if match else None,
            "url": url or None,
            "draft": True,
        }

    def auth_status(self) -> dict[str, Any]:
        return self._gh(["auth", "status"])

    def open_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        *,
        draft: bool = False,
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
            raise ValueError(
                "SACM may push and open pull requests only from sacm/ branches."
            )

    def _repository_name(self) -> str:
        result = self._gh(
            ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
        )
        if result["returncode"] != 0:
            raise RuntimeError(result["stderr"])
        return result["stdout"].strip()

    def _base_ref(self, base: str) -> str | None:
        for candidate in (base, f"origin/{base}"):
            result = self._git(["rev-parse", "--verify", "--quiet", candidate])
            if result["returncode"] == 0:
                return candidate
        return None

    def _open_pull_requests(self, branch_name: str) -> dict[str, Any]:
        result = self._gh(
            [
                "pr",
                "list",
                "--head",
                branch_name,
                "--state",
                "open",
                "--json",
                "number,url,isDraft",
                "--limit",
                "1",
            ]
        )
        if result["returncode"] != 0:
            return self._failed(result)
        try:
            payload = json.loads(result["stdout"] or "[]")
        except json.JSONDecodeError:
            return {
                "status": "failed",
                "error": "GitHub returned invalid pull-request JSON.",
            }
        if not isinstance(payload, list):
            return {
                "status": "failed",
                "error": "GitHub returned an unexpected pull-request payload.",
            }
        return {"status": "ok", "pull_requests": payload}

    @staticmethod
    def _is_github_remote(remote_url: str) -> bool:
        expected_hosts = {"github.com"}
        configured_host = os.getenv("GH_HOST")
        if configured_host:
            expected_hosts.add(configured_host.lower().strip("/"))
        if "://" in remote_url:
            host = urlsplit(remote_url).hostname
        else:
            match = re.match(r"(?:[^@]+@)?([^:]+):", remote_url)
            host = match.group(1) if match else None
        normalized_host = host.lower() if host else ""
        return normalized_host in expected_hosts

    @staticmethod
    def _failed(result: dict[str, Any]) -> dict[str, Any]:
        message = str(
            result.get("stderr")
            or result.get("stdout")
            or "GitHub delivery failed."
        )[:2_000]
        return {
            "status": "failed",
            "error": _SECRET.sub("[REDACTED]", message),
        }

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
                timeout=120,
                check=False,
            )
        except FileNotFoundError:
            return {
                "returncode": 127,
                "stdout": "",
                "stderr": f"Command not found: {command[0]}",
            }
        except subprocess.TimeoutExpired:
            return {
                "returncode": 124,
                "stdout": "",
                "stderr": f"Command timed out after 120 seconds: {command[0]}",
            }
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8_000:],
            "stderr": completed.stderr[-8_000:],
        }
