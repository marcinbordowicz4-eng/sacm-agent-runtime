from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from sacm.core.run_context_service import RunContextService
from sacm.infrastructure.db.models import (
    ContextEvent,
    EvidencePack,
    ExecutionPlan,
    Run,
    RunReplay,
    RunSnapshot,
    RunStep,
    RuntimeEvent,
)


class SnapshotService:
    """Creates, validates, restores, and replays durable run checkpoints."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def available(self) -> bool:
        return "run_snapshots" in inspect(self.db.connection()).get_table_names()

    def list_snapshots(self, run_id: str) -> list[RunSnapshot]:
        self._require_run(run_id)
        return (
            self.db.query(RunSnapshot)
            .filter(RunSnapshot.run_id == run_id)
            .order_by(RunSnapshot.event_sequence, RunSnapshot.created_at)
            .all()
        )

    def get(self, run_id: str, snapshot_id: str) -> RunSnapshot | None:
        return (
            self.db.query(RunSnapshot)
            .filter(
                RunSnapshot.id == snapshot_id,
                RunSnapshot.run_id == run_id,
            )
            .first()
        )

    def create(self, run_id: str, reason: str) -> RunSnapshot:
        run = self._require_run(run_id)
        steps = self._steps(run_id)
        if any(step.status == "RUNNING" for step in steps):
            raise ValueError("A snapshot cannot be created while a step is running.")
        self.db.flush()
        event = (
            self.db.query(RuntimeEvent)
            .filter(RuntimeEvent.run_id == run_id)
            .order_by(RuntimeEvent.sequence.desc())
            .first()
        )
        if event is None:
            raise ValueError("A run snapshot requires at least one persisted event.")
        parent = (
            self.db.query(RunSnapshot)
            .filter(
                RunSnapshot.run_id == run_id,
                RunSnapshot.event_sequence < event.sequence,
            )
            .order_by(
                RunSnapshot.event_sequence.desc(),
                RunSnapshot.created_at.desc(),
            )
            .first()
        )
        content = self._content(
            run,
            steps,
            event,
            reason=reason,
            parent_snapshot_id=parent.id if parent else None,
        )
        checksum = self._checksum(content)
        existing = (
            self.db.query(RunSnapshot)
            .filter(
                RunSnapshot.run_id == run_id,
                RunSnapshot.checksum == checksum,
            )
            .first()
        )
        if existing:
            return existing
        snapshot_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"https://sacm.dev/snapshots/{run_id}/{checksum}",
            )
        )
        snapshot = RunSnapshot(
            id=snapshot_id,
            organization_id=run.organization_id,
            project_id=run.project_id,
            tenant_attribution=(
                {
                    "schema_version": "tenant-attribution/v1",
                    "source": "snapshot/run bridge",
                }
                if run.organization_id or run.project_id
                else None
            ),
            checksum=checksum,
            **content,
        )
        self.db.add(snapshot)
        self.db.flush()
        return snapshot

    def validate(self, run: Run, snapshot: RunSnapshot) -> None:
        if snapshot.run_id != run.id or snapshot.task_id != run.task_id:
            raise ValueError("Snapshot run identity does not match the selected run.")
        if snapshot.workflow_version != run.workflow_version:
            raise ValueError("Snapshot is stale for the run workflow version.")
        run_state = snapshot.run_state
        if (
            run_state.get("run_id") != run.id
            or run_state.get("task_id") != run.task_id
            or run_state.get("source_revision") != run.source_revision
        ):
            raise ValueError("Snapshot is stale for the current run identity.")
        content = {
            "schema_version": snapshot.schema_version,
            "run_id": snapshot.run_id,
            "task_id": snapshot.task_id,
            "event_sequence": snapshot.event_sequence,
            "event_hash": snapshot.event_hash,
            "workflow_version": snapshot.workflow_version,
            "run_state": snapshot.run_state,
            "step_state": snapshot.step_state,
            "execution_plan_summary": snapshot.execution_plan_summary,
            "context_summary": snapshot.context_summary,
            "parent_snapshot_id": snapshot.parent_snapshot_id,
            "creation_reason": snapshot.creation_reason,
        }
        if not hmac.compare_digest(snapshot.checksum, self._checksum(content)):
            raise ValueError("Snapshot checksum validation failed.")
        self._validate_event_chain(run.id, snapshot)

    def restore(
        self,
        run_id: str,
        snapshot_id: str,
        reason: str,
    ) -> tuple[Run, list[str]]:
        from sacm.core.run_service import RunService

        run = self._require_run(run_id)
        snapshot = self._require_snapshot(run_id, snapshot_id)
        self.validate(run, snapshot)
        if run.status != "FAILED":
            raise ValueError(
                "Snapshot restore requires a FAILED run; use resume for ordinary "
                "FAILED-run continuation."
            )
        if snapshot.run_state["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError("The selected snapshot is not a resumable checkpoint.")
        captured = {item["id"]: item for item in snapshot.step_state}
        current = {step.id: step for step in self._steps(run_id)}
        if set(captured) != set(current):
            raise ValueError(
                "Snapshot is stale because the run step topology has changed."
            )
        if any(item["status"] == "RUNNING" for item in captured.values()):
            raise ValueError("A snapshot containing a running step cannot be restored.")

        state = snapshot.run_state
        run.status = state["status"]
        run.cancellation_requested = state["cancellation_requested"]
        run.started_at = self._parse_datetime(state.get("started_at"))
        run.completed_at = self._parse_datetime(state.get("completed_at"))
        run.updated_at = datetime.utcnow()
        for step_id, step in current.items():
            item = captured[step_id]
            step.status = item["status"]
            step.input_ = item["input"]
            step.output = item.get("output")
            step.retry_count = item["retry_count"]
            step.started_at = self._parse_datetime(item.get("started_at"))
            step.completed_at = self._parse_datetime(item.get("completed_at"))

        RunService(self.db)._append_event(
            run,
            event_type="SnapshotRestored",
            actor="user",
            payload={
                "schema_version": "snapshot-restore/v1",
                "snapshot_id": snapshot.id,
                "snapshot_checksum": snapshot.checksum,
                "reason": reason,
                "restored_status": run.status,
                "restored_step_ids": sorted(captured),
            },
        )
        restored = self.create(run.id, f"snapshot_restored:{snapshot.id}")
        self.db.commit()
        self.db.refresh(run)
        self.db.refresh(restored)
        return run, sorted(captured)

    def replay(
        self,
        run_id: str,
        snapshot_id: str,
        reason: str,
        overrides: dict[str, Any],
    ) -> RunReplay:
        from sacm.core.run_service import RunService

        source = self._require_run(run_id)
        snapshot = self._require_snapshot(run_id, snapshot_id)
        self.validate(source, snapshot)
        replay = Run(
            id=str(uuid.uuid4()),
            task=source.task,
            organization_id=source.organization_id,
            project_id=source.project_id,
            tenant_attribution=source.tenant_attribution,
            data_region=source.data_region,
            data_classification=source.data_classification,
            status="PLANNING",
            workflow_version=source.workflow_version,
            source_revision=source.source_revision,
            target_repo_path=source.target_repo_path,
            started_at=datetime.utcnow(),
        )
        self.db.add(replay)
        self.db.flush()
        for item in snapshot.step_state:
            self.db.add(
                RunStep(
                    id=str(uuid.uuid4()),
                    run_id=replay.id,
                    sequence=item["sequence"],
                    name=item["name"],
                    status="PENDING",
                    idempotency_key=self._replay_idempotency_key(
                        item["idempotency_key"], source.id, replay.id
                    ),
                    input_=item["input"],
                    output=None,
                    retry_count=0,
                )
            )
        link = RunReplay(
            organization_id=source.organization_id,
            project_id=source.project_id,
            tenant_attribution=(
                {
                    "schema_version": "tenant-attribution/v1",
                    "source": "replay/source run bridge",
                }
                if source.organization_id or source.project_id
                else None
            ),
            source_run_id=source.id,
            source_snapshot_id=snapshot.id,
            replay_run_id=replay.id,
            overrides={key: value for key, value in overrides.items() if value is not None},
            replay_reason=reason,
        )
        self.db.add(link)
        RunService(self.db)._append_event(
            replay,
            event_type="ReplayCreated",
            actor="user",
            payload={
                "schema_version": "run-replay/v1",
                "source_run_id": source.id,
                "source_snapshot_id": snapshot.id,
                "source_snapshot_checksum": snapshot.checksum,
                "overrides": link.overrides,
                "replay_reason": reason,
            },
        )
        self.db.flush()
        self.create(replay.id, f"replay_initialized:{snapshot.id}")
        self.db.commit()
        self.db.refresh(link)
        return link

    def comparison(self, replay_run_id: str) -> dict[str, Any]:
        link = (
            self.db.query(RunReplay)
            .filter(RunReplay.replay_run_id == replay_run_id)
            .first()
        )
        if link is None:
            raise ValueError("Run is not linked to a replay comparison.")
        source = self._require_run(link.source_run_id)
        replay = self._require_run(link.replay_run_id)
        if replay.status == "COMPLETED":
            comparison_status = "completed"
        elif replay.status in {"FAILED", "CANCELLED"}:
            comparison_status = "failed"
        else:
            comparison_status = "in_progress"
        return {
            "schema_version": "replay-comparison/v1",
            "replay_id": link.id,
            "source_run_id": source.id,
            "source_snapshot_id": link.source_snapshot_id,
            "replay_run_id": replay.id,
            "replay_reason": link.replay_reason,
            "overrides": link.overrides,
            "comparison_status": comparison_status,
            "source": self._comparison_side(source),
            "replay": self._comparison_side(replay),
        }

    def replay_metadata(self, run_id: str) -> dict[str, Any] | None:
        link = (
            self.db.query(RunReplay)
            .filter(RunReplay.replay_run_id == run_id)
            .first()
        )
        if link is None:
            return None
        return {
            "schema_version": link.schema_version,
            "source_run_id": link.source_run_id,
            "source_snapshot_id": link.source_snapshot_id,
            "overrides": link.overrides,
            "replay_reason": link.replay_reason,
        }

    def latest_metadata(self, run_id: str) -> dict[str, Any] | None:
        snapshot = (
            self.db.query(RunSnapshot)
            .filter(RunSnapshot.run_id == run_id)
            .order_by(
                RunSnapshot.event_sequence.desc(),
                RunSnapshot.created_at.desc(),
            )
            .first()
        )
        if snapshot is None:
            return None
        return {
            "id": snapshot.id,
            "schema_version": snapshot.schema_version,
            "event_sequence": snapshot.event_sequence,
            "event_hash": snapshot.event_hash,
            "checksum": snapshot.checksum,
            "creation_reason": snapshot.creation_reason,
        }

    def _content(
        self,
        run: Run,
        steps: list[RunStep],
        event: RuntimeEvent,
        *,
        reason: str,
        parent_snapshot_id: str | None,
    ) -> dict[str, Any]:
        plan = (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.task_id == run.task_id)
            .order_by(ExecutionPlan.revision.desc())
            .first()
        )
        context = self._json_safe(
            RunContextService(self.db).build(
                run, include_snapshot_metadata=False
            )
        )
        return {
            "schema_version": "run-snapshot/v1",
            "run_id": run.id,
            "task_id": run.task_id,
            "event_sequence": event.sequence,
            "event_hash": event.event_hash,
            "workflow_version": run.workflow_version,
            "run_state": {
                "run_id": run.id,
                "task_id": run.task_id,
                "status": run.status,
                "source_revision": run.source_revision,
                "target_repo_path": run.target_repo_path,
                "cancellation_requested": run.cancellation_requested,
                "started_at": self._iso(run.started_at),
                "completed_at": self._iso(run.completed_at),
            },
            "step_state": [
                {
                    "id": step.id,
                    "sequence": step.sequence,
                    "name": step.name,
                    "status": step.status,
                    "idempotency_key": step.idempotency_key,
                    "input": step.input_,
                    "output": step.output,
                    "retry_count": step.retry_count,
                    "started_at": self._iso(step.started_at),
                    "completed_at": self._iso(step.completed_at),
                }
                for step in steps
            ],
            "execution_plan_summary": (
                {
                    "id": plan.id,
                    "schema_version": plan.schema_version,
                    "revision": plan.revision,
                    "status": plan.status,
                    "source_hash": plan.source_hash,
                    "application_context_id": plan.application_context_id,
                    "step_count": len(plan.steps),
                }
                if plan
                else None
            ),
            "context_summary": context,
            "parent_snapshot_id": parent_snapshot_id,
            "creation_reason": reason,
        }

    def _validate_event_chain(
        self, run_id: str, snapshot: RunSnapshot
    ) -> None:
        events = (
            self.db.query(RuntimeEvent)
            .filter(
                RuntimeEvent.run_id == run_id,
                RuntimeEvent.sequence <= snapshot.event_sequence,
            )
            .order_by(RuntimeEvent.sequence)
            .all()
        )
        if len(events) != snapshot.event_sequence:
            raise ValueError("Snapshot event chain is stale or incomplete.")
        previous_hash: str | None = None
        for expected_sequence, event in enumerate(events, start=1):
            payload = {
                "run_id": run_id,
                "step_id": event.step_id,
                "sequence": event.sequence,
                "event_type": event.event_type,
                "actor": event.actor,
                "payload": event.payload,
                "previous_event_hash": event.previous_event_hash,
                "occurred_at": event.occurred_at.isoformat(),
            }
            expected_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if (
                event.sequence != expected_sequence
                or event.previous_event_hash != previous_hash
                or not hmac.compare_digest(event.event_hash, expected_hash)
            ):
                raise ValueError("Snapshot event chain validation failed.")
            previous_hash = event.event_hash
        if (
            not events
            or events[-1].sequence != snapshot.event_sequence
            or not hmac.compare_digest(events[-1].event_hash, snapshot.event_hash)
        ):
            raise ValueError("Snapshot event anchor is stale or corrupt.")

    def _comparison_side(self, run: Run) -> dict[str, Any]:
        steps = self._steps(run.id)
        usage = self._usage(run)
        evidence = (
            self.db.query(EvidencePack)
            .filter(EvidencePack.run_id == run.id)
            .order_by(EvidencePack.created_at)
            .all()
        )
        failures = [
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "step_id": event.step_id,
                "payload": event.payload,
            }
            for event in (
                self.db.query(RuntimeEvent)
                .filter(RuntimeEvent.run_id == run.id)
                .order_by(RuntimeEvent.sequence)
                .all()
            )
            if "fail" in event.event_type.lower()
        ]
        outputs = [
            {"step_id": step.id, "name": step.name, "output": step.output}
            for step in steps
            if step.output is not None
        ]
        return {
            "run_id": run.id,
            "status": run.status,
            "steps": [
                {
                    "sequence": step.sequence,
                    "name": step.name,
                    "status": step.status,
                    "retry_count": step.retry_count,
                }
                for step in steps
            ],
            "cost": {
                "estimated_cost_usd": usage["estimated_cost_usd"],
                "cost_estimation_available": usage["cost_estimation_available"],
            },
            "usage": usage,
            "evidence": [
                {
                    "id": pack.id,
                    "manifest_hash": pack.manifest_hash,
                    "created_at": self._iso(pack.created_at),
                }
                for pack in evidence
            ],
            "failures": failures,
            "output_summary": outputs[-1] if outputs else None,
        }

    def _usage(self, run: Run) -> dict[str, Any]:
        totals: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "cost_estimation_available": False,
            }
        )
        events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == run.task_id,
                ContextEvent.event_type == "agent_result",
            )
            .all()
        )
        for event in events:
            contract = event.payload.get("agent_task_contract") or {}
            if contract.get("run_id") != run.id:
                continue
            for record in event.payload.get("usage", []):
                provider = record.get("provider")
                model = record.get("model")
                if not isinstance(provider, str) or not isinstance(model, str):
                    continue
                total = totals[(provider, model)]
                total["input_tokens"] += int(record.get("input_tokens", 0))
                total["output_tokens"] += int(record.get("output_tokens", 0))
                cost = record.get("estimated_cost_usd")
                if isinstance(cost, (int, float)):
                    total["estimated_cost_usd"] += float(cost)
                    total["cost_estimation_available"] = True
        entries = [
            {"provider": provider, "model": model, **values}
            for (provider, model), values in sorted(totals.items())
        ]
        return {
            "entries": entries,
            "input_tokens": sum(item["input_tokens"] for item in entries),
            "output_tokens": sum(item["output_tokens"] for item in entries),
            "estimated_cost_usd": sum(
                item["estimated_cost_usd"] for item in entries
            ),
            "cost_estimation_available": any(
                item["cost_estimation_available"] for item in entries
            ),
        }

    def _require_run(self, run_id: str) -> Run:
        run = self.db.query(Run).filter(Run.id == run_id).first()
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        return run

    def _require_snapshot(self, run_id: str, snapshot_id: str) -> RunSnapshot:
        snapshot = self.get(run_id, snapshot_id)
        if snapshot is None:
            raise ValueError(f"Snapshot {snapshot_id} not found in run {run_id}")
        return snapshot

    def _steps(self, run_id: str) -> list[RunStep]:
        return (
            self.db.query(RunStep)
            .filter(RunStep.run_id == run_id)
            .order_by(RunStep.sequence)
            .all()
        )

    @staticmethod
    def _checksum(content: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(
            json.dumps(
                value,
                sort_keys=True,
                default=lambda item: item.isoformat()
                if isinstance(item, datetime)
                else str(item),
            )
        )

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    @staticmethod
    def _replay_idempotency_key(
        source_key: str, source_run_id: str, replay_run_id: str
    ) -> str:
        if source_run_id in source_key:
            return source_key.replace(source_run_id, replay_run_id)
        return source_key
