import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from langsmith import Client


def _is_enabled() -> bool:
    return os.getenv("LANGSMITH_TRACING", "false").lower() == "true"


@dataclass
class TaskTrace:
    client: Client | None
    run_id: uuid.UUID | None
    project_name: str

    def record(
        self,
        name: str,
        run_type: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> None:
        if self.client is None or self.run_id is None:
            return

        event_id = uuid.uuid4()
        self.client.create_run(
            id=event_id,
            name=name,
            run_type=run_type,
            inputs=inputs,
            project_name=self.project_name,
            parent_run_id=self.run_id,
        )
        self.client.update_run(event_id, outputs=outputs, end_time=datetime.now(timezone.utc))

    def finish(self, outputs: dict[str, Any]) -> None:
        if self.client is None or self.run_id is None:
            return
        self.client.update_run(
            self.run_id,
            outputs=outputs,
            end_time=datetime.now(timezone.utc),
        )


class ObservabilityService:
    """Privacy-preserving, opt-in operational traces for LangSmith."""

    def __init__(self) -> None:
        self._enabled = _is_enabled()
        self._project_name = os.getenv("LANGSMITH_PROJECT", "sacm-agent-runtime")
        if self._enabled and not os.getenv("LANGSMITH_API_KEY"):
            raise RuntimeError(
                "LANGSMITH_API_KEY must be set when LANGSMITH_TRACING=true."
            )
        self._client = Client() if self._enabled else None

    def start_task(self, task_id: str, max_steps: int) -> TaskTrace:
        if self._client is None:
            return TaskTrace(None, None, self._project_name)

        run_id = uuid.uuid4()
        self._client.create_run(
            id=run_id,
            name="sacm.run_task",
            run_type="chain",
            inputs={"task_id": task_id, "max_steps": max_steps},
            project_name=self._project_name,
            extra={"metadata": {"component": "orchestrator"}},
        )
        return TaskTrace(self._client, run_id, self._project_name)
