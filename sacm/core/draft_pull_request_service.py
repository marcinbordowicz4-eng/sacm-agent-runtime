import os
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from sacm.adapters.github_adapter import GitHubAdapter
from sacm.adapters.repository_adapter import RepositoryAdapter, RepositoryError
from sacm.core.event_service import EventService
from sacm.infrastructure.db.models import ContextEvent, Task


@dataclass(frozen=True)
class TaskBranch:
    worktree_path: str
    branch_name: str


class DraftPullRequestService:
    def __init__(
        self,
        db: Session,
        *,
        github_factory: Callable[[str], GitHubAdapter] = GitHubAdapter,
    ) -> None:
        self.db = db
        self.github_factory = github_factory

    def publish(
        self,
        task_id: str,
        *,
        verified: bool,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        base = {
            "schema_version": "github-draft-pr-delivery/v1",
            "task_id": task_id,
            "run_id": run_id,
            "verified": verified,
        }
        if not verified:
            return {**base, "status": "skipped", "reason": "unverified"}
        if not self._delivery_enabled():
            return {**base, "status": "skipped", "reason": "disabled"}
        task = self.db.get(Task, task_id)
        if task is None:
            raise ValueError("Task not found.")
        target = self._task_branch(task_id, run_id=run_id)
        if target is None:
            return {**base, "status": "skipped", "reason": "no_task_branch"}
        try:
            worktree_path = str(RepositoryAdapter(target.worktree_path).repo_path)
        except RepositoryError as exc:
            return {
                **base,
                "status": "failed",
                "branch_name": target.branch_name,
                "error": str(exc)[:2_000],
            }
        try:
            result = self.github_factory(worktree_path).publish_draft_pull_request(
                title=f"SACM: {task.title}"[:120],
                body=(
                    f"Automated verified SACM change for task `{task.id}`."
                    + (f"\n\nRun: `{run_id}`" if run_id else "")
                    + "\n\nThis pull request is intentionally a draft and requires human review."
                ),
                branch_name=target.branch_name,
                base=os.getenv("SACM_GITHUB_BASE_BRANCH", "main"),
            )
        except Exception as exc:
            result = GitHubAdapter._failed({"stderr": str(exc)})
        return {
            **base,
            "branch_name": target.branch_name,
            **result,
        }

    def record(self, task_id: str, result: dict[str, Any]) -> None:
        EventService(self.db).save(
            task_id,
            "github_draft_pr_delivery",
            result,
        )
        from sacm.core.lifecycle_metric_service import LifecycleMetricService

        LifecycleMetricService(self.db).record(
            "delivery.draft_pr",
            task_id=task_id,
            run_id=result.get("run_id"),
            details={
                key: result.get(key)
                for key in ("status", "outcome", "reason", "draft")
                if result.get(key) is not None
            },
        )

    @staticmethod
    def _delivery_enabled() -> bool:
        configured = os.getenv("SACM_AUTO_DRAFT_PR")
        if configured is None:
            configured = os.getenv("SACM_CODEX_AUTO_CREATE_PR")
        return str(configured or "true").lower() == "true"

    def _task_branch(
        self,
        task_id: str,
        *,
        run_id: str | None = None,
    ) -> TaskBranch | None:
        events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == task_id,
                ContextEvent.event_type == "agent_result",
            )
            .order_by(ContextEvent.created_at.desc(), ContextEvent.id.desc())
            .limit(100)
            .all()
        )
        for event in events:
            event_run_id = event.payload.get("run_id")
            contract = event.payload.get("agent_task_contract")
            if event_run_id is None and isinstance(contract, dict):
                event_run_id = contract.get("run_id")
            if run_id is not None and event_run_id != run_id:
                continue
            actions = event.payload.get("actions")
            if not isinstance(actions, list):
                continue
            for action in reversed(actions):
                if (
                    not isinstance(action, dict)
                    or action.get("type") != "CODEX_EXECUTION"
                ):
                    continue
                path = action.get("worktree_path")
                branch = action.get("branch_name")
                if (
                    isinstance(path, str)
                    and path
                    and isinstance(branch, str)
                    and branch.startswith("sacm/")
                ):
                    return TaskBranch(path, branch)
        return None
