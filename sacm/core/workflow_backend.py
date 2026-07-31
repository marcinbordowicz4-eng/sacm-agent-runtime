import asyncio
import json
import os
from typing import Any, Protocol

from sqlalchemy.orm import Session

from sacm.core.execution_plane_service import ExecutionPlaneService
from sacm.core.external_agent_service import ExternalAgentService
from sacm.core.local_workflow import LocalWorkflow
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import Project
from sacm.schemas.contracts import ExternalAgentStepCreate


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


class RemoteWorkflowBackend:
    """Schedules a signed AgentTaskV1 for an enrolled isolated executor."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def execute(self, run_id: str) -> dict[str, Any]:
        runs = RunService(self.db)
        run = runs.get(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        if run.cancellation_requested or run.status == "CANCELLED":
            raise ValueError(f"Run {run_id} is cancelled")
        if run.status == "CREATED":
            runs.transition(run_id, "PLANNING", "RemoteRunSubmitted")
        elif run.status == "FAILED":
            runs.resume(run_id)
        elif run.status != "PLANNING":
            raise ValueError(f"Run {run_id} cannot schedule from {run.status}")

        project = self.db.get(Project, run.project_id) if run.project_id else None
        repository_coordinate = (
            project.repository_full_name
            if project and project.repository_full_name
            else os.getenv("SACM_CUSTOMER_REPOSITORY_COORDINATE")
        )
        scheduled = ExternalAgentService(self.db).schedule(
            run_id,
            ExternalAgentStepCreate(
                framework="sacm-remote",
                agent_name="workflow-executor",
                idempotency_key=f"{run_id}:remote-workflow",
                role="reasoner",
                objective=run.task.description,
                acceptance_criteria=[
                    "Return a valid AgentResultV1 for the scheduled run step."
                ],
                context_references=[f"task:{run.task_id}", f"run:{run_id}"],
                allowed_tools=[],
                denied_tools=[],
                token_budget=int(os.getenv("SACM_REMOTE_TOKEN_BUDGET", "12000")),
                timeout_seconds=int(
                    os.getenv("SACM_REMOTE_TIMEOUT_SECONDS", "1800")
                ),
                execution_context={
                    "repository_coordinate": repository_coordinate,
                    "source_revision": run.source_revision,
                    "workflow_backend": "remote",
                    "workspace_location": "customer-managed",
                },
            ),
            trusted_internal=True,
        )
        capabilities = [
            item.strip()
            for item in os.getenv(
                "SACM_REMOTE_REQUIRED_CAPABILITIES", "agent-task/v1"
            ).split(",")
            if item.strip()
        ]
        try:
            labels = json.loads(os.getenv("SACM_REMOTE_REQUIRED_LABELS", "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("SACM_REMOTE_REQUIRED_LABELS must be valid JSON.") from exc
        if not isinstance(labels, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in labels.items()
        ):
            raise ValueError(
                "SACM_REMOTE_REQUIRED_LABELS must be a JSON object of strings."
            )
        job = ExecutionPlaneService(self.db).schedule(
            run_id=run_id,
            run_step_id=scheduled.step.id,
            task=scheduled.task,
            idempotency_key=f"{run_id}:remote-workflow",
            required_capabilities=capabilities,
            required_labels=labels,
        )
        return {
            "run_id": run_id,
            "status": "SCHEDULED",
            "backend": "remote",
            "job_id": job.id,
            "job_state": job.state,
            "payload_hash": job.payload_hash,
        }


def workflow_backend(db: Session) -> WorkflowBackend:
    backend = os.getenv("SACM_WORKFLOW_BACKEND", "local").lower()
    if backend == "local":
        return LocalWorkflowBackend(db)
    if backend == "temporal":
        return TemporalWorkflowBackend(db)
    if backend == "remote":
        return RemoteWorkflowBackend(db)
    raise ValueError(f"Unsupported SACM_WORKFLOW_BACKEND: {backend}")
