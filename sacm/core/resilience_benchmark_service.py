import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sacm.adapters.github_adapter import GitHubAdapter
from sacm.adapters.repository_adapter import RepositoryAdapter, RepositoryPathError
from sacm.core.draft_pull_request_service import DraftPullRequestService
from sacm.core.event_service import EventService
from sacm.core.recovery_service import RecoveryService
from sacm.core.run_service import RunService
from sacm.core.workflow_queue_service import WorkflowQueueService
from sacm.infrastructure.db.models import Base
from sacm.schemas.run import RunCreate


@dataclass(frozen=True)
class ResilienceScenarioResult:
    name: str
    status: str
    duration_ms: int
    detail: str


class ResilienceBenchmarkService:
    """Runs deterministic infrastructure scenarios without external model calls."""

    def run(self) -> dict:
        scenarios: list[tuple[str, Callable[[Session, Path], str]]] = [
            ("concurrent_claim", self._concurrent_claim),
            ("lease_expiry_recovery", self._lease_expiry_recovery),
            ("cancellation", self._cancellation),
            ("recovery_fallback", self._recovery_fallback),
            ("safe_patch", self._safe_patch),
            ("delivery_idempotency", self._delivery_idempotency),
        ]
        results: list[ResilienceScenarioResult] = []
        with tempfile.TemporaryDirectory(prefix="sacm-resilience-") as directory:
            root = Path(directory)
            engine = create_engine(f"sqlite:///{root / 'benchmark.db'}")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine)
            try:
                for name, scenario in scenarios:
                    started = time.monotonic()
                    db = factory()
                    try:
                        detail = scenario(db, root)
                        status = "PASS"
                    except Exception as exc:
                        db.rollback()
                        detail = f"{exc.__class__.__name__}: {exc}"
                        status = "FAIL"
                    finally:
                        db.close()
                    results.append(
                        ResilienceScenarioResult(
                            name=name,
                            status=status,
                            duration_ms=int(
                                (time.monotonic() - started) * 1_000
                            ),
                            detail=detail,
                        )
                    )
            finally:
                engine.dispose()
        passed = sum(item.status == "PASS" for item in results)
        return {
            "schema_version": "resilience-benchmark/v1",
            "status": "PASS" if passed == len(results) else "FAIL",
            "passed": passed,
            "total": len(results),
            "scenarios": [item.__dict__ for item in results],
        }

    @staticmethod
    def _run(db: Session, title: str):
        return RunService(db).create(
            RunCreate(title=title, description=f"Benchmark scenario: {title}")
        )

    def _concurrent_claim(self, db: Session, _: Path) -> str:
        run = self._run(db, "concurrent claim")
        queue = WorkflowQueueService(db)
        first = queue.submit(run.id)
        repeated = queue.submit(run.id)
        claimed = queue.claim()
        second_claim = queue.claim()
        if first.id != repeated.id or claimed is None or second_claim is not None:
            raise AssertionError("Queue idempotency or exclusive claim failed.")
        return "One durable job was created and claimed exactly once."

    def _lease_expiry_recovery(self, db: Session, _: Path) -> str:
        run = self._run(db, "lease expiry")
        queue = WorkflowQueueService(db)
        queue.submit(run.id)
        claimed = queue.claim()
        if claimed is None:
            raise AssertionError("Initial claim failed.")
        job = queue.get_for_run(run.id)
        if job is None:
            raise AssertionError("Queued job was not found.")
        job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        reclaimed = queue.claim()
        if reclaimed is None or reclaimed[0].attempt != 2:
            raise AssertionError("Expired lease was not reclaimed.")
        return "Expired work was requeued and reclaimed with attempt 2."

    def _cancellation(self, db: Session, _: Path) -> str:
        run = self._run(db, "cancellation")
        queue = WorkflowQueueService(db)
        queue.submit(run.id)
        RunService(db).cancel(run.id)
        job = queue.cancel(run.id)
        if job is None or job.state != "CANCELLED" or queue.claim() is not None:
            raise AssertionError("Cancelled workflow remained executable.")
        return "Run and queued work reached terminal cancellation."

    def _recovery_fallback(self, db: Session, _: Path) -> str:
        run = self._run(db, "recovery fallback")
        runs = RunService(db)
        runs.transition(run.id, "PLANNING", "BenchmarkPlanning")
        runs.transition(run.id, "IMPLEMENTING", "BenchmarkImplementing")
        step = runs.add_step(run.id, "executor", {}, "benchmark:executor")
        runs.start_step(run.id, step.id)
        runs.fail_step(
            run.id,
            step.id,
            {
                "classification": "ENVIRONMENT",
                "type": "ResourceExhaustion",
                "message": "Executor was killed.",
                "details": {"failure_reason": "INFRASTRUCTURE_RESOURCE"},
            },
        )
        recovered_step, report, decision = RecoveryService(db).handle(
            run.id,
            step.id,
            (
                (
                    current_step.output
                    if (current_step := runs.get_step(run.id, step.id)) is not None
                    else None
                )
                or {}
            )["failure"],
        )
        if report.classification.value != "ENVIRONMENT" or decision.action.value != "RETRY":
            raise AssertionError("Infrastructure failure selected the wrong fallback.")
        return "Resource exhaustion selected bounded infrastructure retry."

    @staticmethod
    def _safe_patch(_: Session, root: Path) -> str:
        repository = root / "patch-repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(
            ["git", "config", "user.email", "benchmark@sacm.invalid"],
            cwd=repository,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "SACM Benchmark"],
            cwd=repository,
            check=True,
        )
        (repository / "app.py").write_text("old\n")
        subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)
        patch = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        adapter = RepositoryAdapter(str(repository))
        adapter.apply_patch(patch)
        unsafe = (
            "diff --git a/../secret b/../secret\n"
            "--- a/../secret\n"
            "+++ b/../secret\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        try:
            adapter.apply_patch(unsafe)
        except RepositoryPathError:
            pass
        else:
            raise AssertionError("Repository traversal patch was accepted.")
        if (repository / "app.py").read_text() != "new\n":
            raise AssertionError("Valid patch was not applied.")
        return "Valid patch applied; traversal patch rejected."

    def _delivery_idempotency(self, db: Session, root: Path) -> str:
        run = self._run(db, "delivery idempotency")
        worktree = root / "delivery-worktree"
        worktree.mkdir()
        EventService(db).save(
            run.task_id,
            "agent_result",
            {
                "agent_task_contract": {"run_id": run.id},
                "actions": [
                    {
                        "type": "CODEX_EXECUTION",
                        "worktree_path": str(worktree),
                        "branch_name": "sacm/benchmark-delivery",
                    }
                ],
            },
        )

        class FakeGitHub(GitHubAdapter):
            calls = 0

            def publish_draft_pull_request(
                self,
                title: str,
                body: str,
                branch_name: str,
                base: str = "main",
            ) -> dict[str, Any]:
                FakeGitHub.calls += 1
                return {
                    "status": "delivered",
                    "outcome": "created" if FakeGitHub.calls == 1 else "reused",
                    "branch": branch_name,
                }

        previous = os.environ.get("SACM_AUTO_DRAFT_PR")
        os.environ["SACM_AUTO_DRAFT_PR"] = "true"
        try:
            service = DraftPullRequestService(
                db, github_factory=lambda path: FakeGitHub(path)
            )
            first = service.publish(run.task_id, verified=True, run_id=run.id)
            second = service.publish(run.task_id, verified=True, run_id=run.id)
        finally:
            if previous is None:
                os.environ.pop("SACM_AUTO_DRAFT_PR", None)
            else:
                os.environ["SACM_AUTO_DRAFT_PR"] = previous
        if first.get("outcome") != "created" or second.get("outcome") != "reused":
            raise AssertionError("Repeated delivery did not reuse the draft PR.")
        return "Repeated publication reused the verified run branch."
