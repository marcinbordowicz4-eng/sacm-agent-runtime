import asyncio
import os
from typing import Any, Protocol

from sqlalchemy.orm import Session

from sacm.core.local_workflow import LocalWorkflow
from sacm.core.run_service import RunService


class WorkflowBackend(Protocol):
    def execute(self, run_id: str) -> dict[str, Any]: ...


class LocalWorkflowBackend:
    def __init__(self, db: Session) -> None:
        self.workflow = LocalWorkflow(db)

    def execute(self, run_id: str) -> dict[str, Any]:
        return self.workflow.execute(run_id)


class TemporalWorkflowBackend:
    """Submits a run to Temporal; a separately deployed worker performs execution."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def execute(self, run_id: str) -> dict[str, Any]:
        run = RunService(self.db).get(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        if run.status == "CREATED":
            RunService(self.db).transition(run_id, "PLANNING", "TemporalRunSubmitted")
        self._submit(run_id)
        return {
            "run_id": run_id,
            "status": "SCHEDULED",
            "backend": "temporal",
            "task_queue": os.getenv("SACM_TEMPORAL_TASK_QUEUE", "sacm-runs"),
        }

    @staticmethod
    def _submit(run_id: str) -> None:
        try:
            from temporalio.client import Client
        except ImportError as exc:
            raise RuntimeError(
                "Temporal support requires: pip install -e '.[temporal]'"
            ) from exc

        async def submit() -> None:
            client = await Client.connect(
                os.getenv("SACM_TEMPORAL_ADDRESS", "localhost:7233"),
                namespace=os.getenv("SACM_TEMPORAL_NAMESPACE", "default"),
            )
            await client.start_workflow(
                "SACMRunWorkflow",
                run_id,
                id=f"sacm-run-{run_id}",
                task_queue=os.getenv("SACM_TEMPORAL_TASK_QUEUE", "sacm-runs"),
            )

        asyncio.run(submit())


def workflow_backend(db: Session) -> WorkflowBackend:
    backend = os.getenv("SACM_WORKFLOW_BACKEND", "local").lower()
    if backend == "local":
        return LocalWorkflowBackend(db)
    if backend == "temporal":
        return TemporalWorkflowBackend(db)
    raise ValueError(f"Unsupported SACM_WORKFLOW_BACKEND: {backend}")
