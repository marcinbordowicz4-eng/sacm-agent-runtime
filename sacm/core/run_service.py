import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import EvidencePack, Run, RunStep, RuntimeEvent, Task
from sacm.schemas.run import RunCreate

_ALLOWED_TRANSITIONS = {
    "CREATED": {"PLANNING", "CANCELLED"},
    "PLANNING": {"AWAITING_APPROVAL", "IMPLEMENTING", "FAILED", "CANCELLED"},
    "AWAITING_APPROVAL": {"IMPLEMENTING", "CANCELLED"},
    "IMPLEMENTING": {"REVIEWING", "FAILED", "CANCELLED"},
    "REVIEWING": {"FIXING", "TESTING", "FAILED", "CANCELLED"},
    "FIXING": {"REVIEWING", "FAILED", "CANCELLED"},
    "TESTING": {"FIXING", "DELIVERING", "FAILED", "CANCELLED"},
    "DELIVERING": {"COMPLETED", "FAILED", "CANCELLED"},
    "FAILED": {"PLANNING", "IMPLEMENTING", "REVIEWING", "TESTING", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RunService:
    """Persistent local workflow state with append-only, hash-chained events."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: RunCreate) -> Run:
        task = Task(
            id=str(uuid.uuid4()),
            title=payload.title,
            description=payload.description,
            target_repo_path=payload.target_repo_path,
            status="pending",
        )
        run = Run(
            id=str(uuid.uuid4()),
            task=task,
            status="CREATED",
            source_revision=payload.source_revision,
            target_repo_path=payload.target_repo_path,
            project_id=(
                payload.project_id
                if payload.project_id and not payload.project_id.startswith("legacy:")
                else None
            ),
        )
        self.db.add(run)
        self._append_event(
            run,
            event_type="RunCreated",
            actor="system",
            payload={"task_id": task.id, "workflow_version": run.workflow_version},
        )
        self.db.commit()
        self.db.refresh(run)
        return run

    def get(self, run_id: str) -> Run | None:
        return self.db.query(Run).filter(Run.id == run_id).first()

    def list_steps(self, run_id: str) -> list[RunStep]:
        return (
            self.db.query(RunStep)
            .filter(RunStep.run_id == run_id)
            .order_by(RunStep.sequence)
            .all()
        )

    def transition(
        self,
        run_id: str,
        status: str,
        event_type: str,
        *,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
        step_id: str | None = None,
    ) -> Run:
        run = self._require(run_id)
        if status != run.status and status not in _ALLOWED_TRANSITIONS[run.status]:
            raise ValueError(f"Invalid transition: {run.status} -> {status}")
        if status == "COMPLETED":
            self._validate_completion(run)

        now = _utcnow()
        run.status = status
        run.updated_at = now
        if status == "PLANNING" and run.started_at is None:
            run.started_at = now
        if status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            run.completed_at = None
        if status in {"COMPLETED", "FAILED", "CANCELLED"}:
            run.completed_at = now
        self._append_event(
            run,
            event_type=event_type,
            actor=actor,
            payload=payload or {},
            step_id=step_id,
        )
        self.db.commit()
        self.db.refresh(run)
        return run

    def _validate_completion(self, run: Run) -> None:
        pack = (
            self.db.query(EvidencePack)
            .filter(EvidencePack.run_id == run.id)
            .order_by(EvidencePack.created_at.desc())
            .first()
        )
        if pack is None:
            raise ValueError(
                "A run cannot complete until a hash-checked evidence pack is recorded."
            )
        incomplete_steps = [
            step.id for step in self.list_steps(run.id) if step.status != "COMPLETED"
        ]
        if incomplete_steps:
            raise ValueError(
                "A run cannot complete while steps remain incomplete: "
                + ", ".join(incomplete_steps)
            )

    def add_step(
        self,
        run_id: str,
        name: str,
        input_: dict[str, Any],
        idempotency_key: str,
    ) -> RunStep:
        run = self._require(run_id)
        existing = (
            self.db.query(RunStep)
            .filter(
                RunStep.run_id == run_id,
                RunStep.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing:
            return existing
        sequence = len(self.list_steps(run_id)) + 1
        step = RunStep(
            id=str(uuid.uuid4()),
            run_id=run_id,
            sequence=sequence,
            name=name,
            input_=input_,
            idempotency_key=idempotency_key,
        )
        self.db.add(step)
        self._append_event(
            run,
            event_type="StepScheduled",
            actor="system",
            payload={"name": name, "idempotency_key": idempotency_key},
            step_id=step.id,
        )
        self.db.commit()
        self.db.refresh(step)
        return step

    def start_step(self, run_id: str, step_id: str) -> RunStep:
        run = self._require(run_id)
        step = self._require_step(run_id, step_id)
        if step.status == "RUNNING":
            return step
        if step.status != "PENDING":
            raise ValueError(f"Step {step_id} cannot start from {step.status}")
        step.status = "RUNNING"
        step.started_at = _utcnow()
        self._append_event(
            run,
            event_type="StepStarted",
            actor="system",
            payload={"name": step.name},
            step_id=step.id,
        )
        self.db.commit()
        self.db.refresh(step)
        return step

    def complete_step(
        self, run_id: str, step_id: str, output: dict[str, Any]
    ) -> RunStep:
        run = self._require(run_id)
        step = self._require_step(run_id, step_id)
        if step.status == "COMPLETED":
            return step
        if step.status not in {"PENDING", "RUNNING"}:
            raise ValueError(f"Step {step_id} cannot complete from {step.status}")
        step.status = "COMPLETED"
        step.output = output
        step.completed_at = _utcnow()
        self._append_event(
            run,
            event_type="StepCompleted",
            actor="system",
            payload={"name": step.name},
            step_id=step.id,
        )
        self.db.commit()
        self.db.refresh(step)
        return step

    def fail_step(
        self, run_id: str, step_id: str, failure: dict[str, Any]
    ) -> RunStep:
        run = self._require(run_id)
        step = self._require_step(run_id, step_id)
        step.status = "FAILED"
        step.output = {"failure": failure}
        step.completed_at = _utcnow()
        self._append_event(
            run,
            event_type="StepFailed",
            actor="system",
            payload={"name": step.name, "failure": failure},
            step_id=step.id,
        )
        self.db.commit()
        self.db.refresh(step)
        return step

    def cancel(self, run_id: str) -> Run:
        run = self._require(run_id)
        if run.status in {"COMPLETED", "CANCELLED"}:
            raise ValueError(f"Run {run_id} cannot be cancelled from {run.status}")
        run.cancellation_requested = True
        return self.transition(run_id, "CANCELLED", "RunCancelled", actor="user")

    def resume(self, run_id: str) -> Run:
        run = self._require(run_id)
        if run.status != "FAILED":
            raise ValueError("Only failed runs can be resumed.")
        return self.transition(run_id, "PLANNING", "RunResumed", actor="user")

    def retry_step(self, run_id: str, step_id: str) -> RunStep:
        run = self._require(run_id)
        step = self._require_step(run_id, step_id)
        if step.status != "FAILED":
            raise ValueError("Only failed steps can be retried.")
        step.status = "PENDING"
        step.retry_count += 1
        step.started_at = None
        step.completed_at = None
        self._append_event(
            run,
            event_type="StepRetryScheduled",
            actor="user",
            payload={"name": step.name, "retry_count": step.retry_count},
            step_id=step.id,
        )
        self.db.commit()
        self.db.refresh(step)
        return step

    def recover_interrupted(self, run_id: str) -> Run:
        """Make an interrupted in-process execution resumable after a restart."""
        run = self._require(run_id)
        interrupted_steps = [
            step for step in self.list_steps(run_id) if step.status == "RUNNING"
        ]
        if not interrupted_steps:
            return run
        for step in interrupted_steps:
            self.fail_step(
                run_id,
                step.id,
                {
                    "type": "ProcessInterrupted",
                    "message": "The runtime restarted while this step was running.",
                },
            )
        if run.status not in {"FAILED", "CANCELLED", "COMPLETED"}:
            return self.transition(
                run_id,
                "FAILED",
                "RunRecoveryDetected",
                payload={"interrupted_step_ids": [step.id for step in interrupted_steps]},
            )
        return run

    def events(self, run_id: str) -> list[RuntimeEvent]:
        return (
            self.db.query(RuntimeEvent)
            .filter(RuntimeEvent.run_id == run_id)
            .order_by(RuntimeEvent.sequence)
            .all()
        )

    def _append_event(
        self,
        run: Run,
        *,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        step_id: str | None = None,
    ) -> RuntimeEvent:
        previous = (
            self.db.query(RuntimeEvent)
            .filter(RuntimeEvent.run_id == run.id)
            .order_by(RuntimeEvent.sequence.desc())
            .first()
        )
        sequence = 1 if previous is None else previous.sequence + 1
        previous_hash = previous.event_hash if previous else None
        occurred_at = _utcnow()
        event_payload = {
            "run_id": run.id,
            "step_id": step_id,
            "sequence": sequence,
            "event_type": event_type,
            "actor": actor,
            "payload": payload,
            "previous_event_hash": previous_hash,
            "occurred_at": occurred_at.isoformat(),
        }
        event_hash = hashlib.sha256(
            json.dumps(event_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        event = RuntimeEvent(
            run_id=run.id,
            step_id=step_id,
            sequence=sequence,
            event_type=event_type,
            actor=actor,
            correlation_id=run.id,
            payload=payload,
            previous_event_hash=previous_hash,
            event_hash=event_hash,
            occurred_at=occurred_at,
        )
        self.db.add(event)
        return event

    def _require(self, run_id: str) -> Run:
        run = self.get(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        return run

    def _require_step(self, run_id: str, step_id: str) -> RunStep:
        step = (
            self.db.query(RunStep)
            .filter(RunStep.run_id == run_id, RunStep.id == step_id)
            .first()
        )
        if not step:
            raise ValueError(f"Step {step_id} not found in run {run_id}")
        return step
