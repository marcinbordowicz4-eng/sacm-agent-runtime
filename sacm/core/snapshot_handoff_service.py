import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.run_service import RunService
from sacm.core.snapshot_service import SnapshotService
from sacm.infrastructure.db.models import SnapshotHandoff, SnapshotScopeLease


class SnapshotHandoffError(ValueError):
    pass


class SnapshotHandoffService:
    """Hands off only immutable checkpoints and fences stale scope owners."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        run_id: str,
        snapshot_id: str,
        *,
        base_sha: str,
        head_sha: str,
        context_snapshot_id: str,
        closed_subtasks: list[str],
        open_subtasks: list[str],
        changed_symbols: list[str],
        quorum_notes: list[dict[str, Any]],
        evidence_hashes: list[str],
    ) -> SnapshotHandoff:
        snapshot = SnapshotService(self.db).get(run_id, snapshot_id)
        run = RunService(self.db).get(run_id)
        if snapshot is None or run is None:
            raise SnapshotHandoffError("Run snapshot not found.")
        SnapshotService(self.db).validate(run, snapshot)
        manifest = {
            "schema_version": "snapshot-handoff/v1",
            "snapshot_id": snapshot.id,
            "snapshot_checksum": snapshot.checksum,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "context_snapshot_id": context_snapshot_id,
            "closed_subtasks": sorted(set(closed_subtasks)),
            "open_subtasks": sorted(set(open_subtasks)),
            "changed_symbols": sorted(set(changed_symbols)),
            "quorum_notes": quorum_notes,
            "evidence_hashes": sorted(set(evidence_hashes)),
        }
        if set(manifest["closed_subtasks"]) & set(manifest["open_subtasks"]):
            raise SnapshotHandoffError("A subtask cannot be both closed and open.")
        manifest_hash = self._hash(manifest)
        existing = (
            self.db.query(SnapshotHandoff)
            .filter(
                SnapshotHandoff.snapshot_id == snapshot.id,
                SnapshotHandoff.manifest_hash == manifest_hash,
            )
            .first()
        )
        if existing is not None:
            return existing
        handoff = SnapshotHandoff(
            run_id=run_id,
            snapshot_id=snapshot.id,
            manifest=manifest,
            manifest_hash=manifest_hash,
        )
        self.db.add(handoff)
        self.db.commit()
        self.db.refresh(handoff)
        return handoff

    def accept(self, handoff_id: str, *, evaluator: str) -> SnapshotHandoff:
        handoff = self.db.get(SnapshotHandoff, handoff_id)
        if handoff is None:
            raise SnapshotHandoffError("Snapshot handoff not found.")
        if handoff.status == "ACCEPTED":
            return handoff
        if handoff.status != "PENDING":
            raise SnapshotHandoffError(f"Handoff cannot be accepted from {handoff.status}.")
        handoff.status = "ACCEPTED"
        handoff.accepted_by = evaluator
        handoff.accepted_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(handoff)
        return handoff

    def claim_scope(
        self,
        handoff_id: str,
        *,
        scope_key: str,
        owner: str,
        lease_seconds: int = 900,
    ) -> SnapshotScopeLease:
        handoff = self.db.get(SnapshotHandoff, handoff_id)
        if handoff is None or handoff.status != "ACCEPTED":
            raise SnapshotHandoffError("Only an accepted handoff can be claimed.")
        if scope_key not in handoff.manifest["open_subtasks"]:
            raise SnapshotHandoffError("Scope is not an open subtask in this handoff.")
        now = datetime.utcnow()
        lease = (
            self.db.query(SnapshotScopeLease)
            .filter(
                SnapshotScopeLease.run_id == handoff.run_id,
                SnapshotScopeLease.scope_key == scope_key,
            )
            .first()
        )
        if lease is not None and lease.expires_at > now and lease.owner != owner:
            raise SnapshotHandoffError("Scope is leased by another agent.")
        if lease is None:
            lease = SnapshotScopeLease(
                run_id=handoff.run_id,
                scope_key=scope_key,
                owner=owner,
                handoff_id=handoff.id,
                expires_at=now + timedelta(seconds=lease_seconds),
            )
            self.db.add(lease)
        else:
            lease.owner = owner
            lease.handoff_id = handoff.id
            lease.fencing_token += 1
            lease.expires_at = now + timedelta(seconds=lease_seconds)
        self.db.commit()
        self.db.refresh(lease)
        return lease

    def validate_fencing(
        self, lease_id: str, *, owner: str, fencing_token: int
    ) -> SnapshotScopeLease:
        lease = self.db.get(SnapshotScopeLease, lease_id)
        if (
            lease is None
            or lease.owner != owner
            or lease.fencing_token != fencing_token
            or lease.expires_at <= datetime.utcnow()
        ):
            raise SnapshotHandoffError("Scope lease is stale or no longer owned.")
        return lease

    @staticmethod
    def _hash(value: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
