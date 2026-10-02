"""Project-wide temporal provenance and context reconstruction.

Git remains the source of code truth.  This module stores only the durable
evidence around Git: append-only events, immutable graph edges, semantic
memories, and materialized snapshots.
"""

import hashlib
import json
import re
import subprocess
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Iterable

from sqlalchemy.orm import Session

from sacm.adapters.repository_adapter import RepositoryAdapter, RepositoryError
from sacm.infrastructure.db.models import (
    CognitiveEvent,
    CognitiveMemory,
    CognitiveRelation,
    CognitiveSnapshot,
    Project,
)
from sacm.schemas.cognitive_state import (
    AgentHandoffCreateV1,
    AgentHandoffV1,
    CognitiveContextV1,
    CognitiveDeliveryPassportV1,
    CognitiveEventCreateV1,
    CognitiveEventV1,
    CognitiveRelationCreateV1,
    CognitiveSnapshotV1,
    CommitIngestV1,
    ExplainFileV1,
    GitCommitAnalyzeV1,
    ImpactAnalysisV1,
)


class CognitiveStateError(ValueError):
    pass


class CognitiveStateNotFoundError(CognitiveStateError):
    pass


_SECRET_FIELD = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|authorization|credential|password|"
    r"private[_-]?key|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_TOKEN_VALUE = re.compile(
    r"(?:sk|ghp|github_pat|xox[baprs])[-_A-Za-z0-9]{16,}|"
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----",
    re.IGNORECASE,
)
_METADATA_LINE = re.compile(r"^\s*([A-Z][A-Z_ -]+):\s*(.+?)\s*$")
_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
_TEST_PATH = re.compile(r"(?:^|/)(?:tests?|test_[^/]+|[^/]+_test\.[^/]+)(?:/|$)")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CognitiveStateService:
    def __init__(
        self,
        db: Session,
        embedding_service: Any | None = None,
    ) -> None:
        self.db = db
        # Embedding infrastructure is deliberately optional: event and graph
        # persistence must succeed even if asynchronous vector indexing is down.
        self.embedding_service = embedding_service

    def record_event(
        self,
        project_id: str,
        payload: CognitiveEventCreateV1,
        *,
        commit: bool = True,
    ) -> CognitiveEvent:
        self._lock_project(project_id)
        if payload.parent_event_id and not self.db.get(CognitiveEvent, payload.parent_event_id):
            raise CognitiveStateNotFoundError("Parent cognitive event not found.")
        previous = (
            self.db.query(CognitiveEvent)
            .filter(CognitiveEvent.project_id == project_id)
            .order_by(CognitiveEvent.created_at.desc(), CognitiveEvent.id.desc())
            .first()
        )
        created_at = _utcnow()
        safe_payload = self._sanitize(payload.payload)
        canonical = {
            "project_id": project_id,
            "event_type": payload.event_type,
            "agent_id": payload.agent_id,
            "commit_hash": payload.commit_hash,
            "snapshot_id": payload.snapshot_id,
            "trace_id": payload.trace_id,
            "parent_event_id": payload.parent_event_id,
            "payload": safe_payload,
            "previous_event_hash": previous.event_hash if previous else None,
            "created_at": created_at.isoformat(timespec="microseconds"),
        }
        stored = {key: value for key, value in canonical.items() if key != "created_at"}
        event = CognitiveEvent(
            id=str(uuid.uuid4()),
            **stored,
            event_hash=self._hash(canonical),
            created_at=created_at,
        )
        self.db.add(event)
        self.db.flush()
        if commit:
            self.db.commit()
            self.db.refresh(event)
        return event

    def add_relation(
        self,
        project_id: str,
        payload: CognitiveRelationCreateV1,
        *,
        actor_id: str | None = None,
    ) -> CognitiveRelation:
        self._project(project_id)
        existing = (
            self.db.query(CognitiveRelation)
            .filter_by(
                project_id=project_id,
                source_type=payload.source_type.upper(),
                source_id=payload.source_id,
                target_type=payload.target_type.upper(),
                target_id=payload.target_id,
                relation=payload.relation.upper(),
                commit_hash=payload.commit_hash,
            )
            .order_by(CognitiveRelation.valid_from.desc())
            .first()
        )
        if existing is not None:
            return existing
        event = self.record_event(
            project_id,
            CognitiveEventCreateV1(
                event_type="DEPENDENCY_CREATED",
                agent_id=actor_id,
                commit_hash=payload.commit_hash,
                payload={"relation": self._relation_payload(payload)},
            ),
            commit=False,
        )
        relation = self._relation(project_id, payload, event)
        self.db.add(relation)
        self.db.commit()
        self.db.refresh(relation)
        return relation

    def ingest_commit(self, project_id: str, payload: CommitIngestV1) -> dict[str, Any]:
        """Idempotently project an analyzed Git commit into cognitive evidence."""
        self._project(project_id)
        existing = (
            self.db.query(CognitiveEvent)
            .filter(
                CognitiveEvent.project_id == project_id,
                CognitiveEvent.event_type == "COMMIT_CREATED",
                CognitiveEvent.commit_hash == payload.commit_hash,
            )
            .first()
        )
        if existing is not None:
            snapshot = (
                self.db.query(CognitiveSnapshot)
                .filter(
                    CognitiveSnapshot.project_id == project_id,
                    CognitiveSnapshot.commit_hash == payload.commit_hash,
                )
                .order_by(CognitiveSnapshot.created_at.desc())
                .first()
            )
            return {
                "event": self._event_read(existing),
                "snapshot": self._snapshot_read(snapshot) if snapshot else None,
                "idempotent": True,
            }

        metadata = self._message_metadata(payload.message)
        requirements = self._merge_ids(payload.requirements, metadata.get("REQ", []))
        decisions = self._merge_ids(payload.decisions, metadata.get("DECISION", []))
        skills = self._merge_ids(payload.skills, metadata.get("SKILLS", []))
        agent_id = payload.agent_id or self._first(metadata.get("AGENT", []))
        files = [self._safe_path(path) for path in payload.files]
        tests = [self._safe_path(path) for path in payload.tests]
        event = self.record_event(
            project_id,
            CognitiveEventCreateV1(
                event_type="COMMIT_CREATED",
                agent_id=agent_id,
                commit_hash=payload.commit_hash,
                trace_id=payload.trace_id,
                payload={
                    "parent_hash": payload.parent_hash,
                    "branch": payload.branch,
                    "message": payload.message,
                    "requirements": requirements,
                    "decisions": decisions,
                    "skills": skills,
                    "files": files,
                    "tests": tests,
                    "dependencies": payload.dependencies,
                    "risk": payload.risk,
                },
            ),
            commit=False,
        )
        relations: list[CognitiveRelation] = []

        def relate(source_type: str, source_id: str, target_type: str, target_id: str, relation: str) -> None:
            relation_payload = CognitiveRelationCreateV1(
                source_type=source_type,
                source_id=source_id,
                target_type=target_type,
                target_id=target_id,
                relation=relation,
                commit_hash=payload.commit_hash,
            )
            item = self._relation(project_id, relation_payload, event)
            self.db.add(item)
            relations.append(item)

        for requirement in requirements:
            relate("REQUIREMENT", requirement, "COMMIT", payload.commit_hash, "IMPLEMENTED_BY")
        for decision in decisions:
            relate("COMMIT", payload.commit_hash, "DECISION", decision, "BASED_ON")
        if agent_id:
            relate("COMMIT", payload.commit_hash, "AGENT", agent_id, "CREATED_BY")
        for skill in skills:
            relate("COMMIT", payload.commit_hash, "SKILL", skill, "USED_SKILL")
        for file_path in files:
            relate("COMMIT", payload.commit_hash, "FILE", file_path, "MODIFIES")
        for test in tests:
            relate("COMMIT", payload.commit_hash, "TEST", test, "VALIDATED_BY")
        for source, target in payload.dependencies:
            relate("FILE", self._safe_path(source), "FILE", self._safe_path(target), "DEPENDS_ON")

        summary = payload.semantic_summary or self._summary(
            payload.commit_hash, requirements, decisions, files, tests, payload.message
        )
        memory = self._memory(
            project_id,
            content=summary,
            memory_type="COMMIT_SUMMARY",
            agent_id=agent_id,
            commit_hash=payload.commit_hash,
            importance=0.8 if payload.risk in {"HIGH", "CRITICAL"} else 0.6,
            confidence=0.9,
            created_at=event.created_at,
        )
        self.db.add(memory)
        self.db.flush()
        self.db.commit()
        snapshot = self.create_snapshot(
            project_id,
            commit_hash=payload.commit_hash,
            snapshot_type="MICRO",
            reason="commit_ingested",
        )
        return {
            "event": self._event_read(event),
            "snapshot": self._snapshot_read(snapshot),
            "relations_created": len(relations),
            "memory_id": memory.id,
            "idempotent": False,
        }

    def analyze_and_ingest_git_commit(
        self, project_id: str, payload: GitCommitAnalyzeV1
    ) -> dict[str, Any]:
        """Collect an immutable Git commit from the project's configured repository."""
        project = self._project(project_id)
        if not project.repository_path:
            raise CognitiveStateError("Project does not have a configured repository path.")
        try:
            repository = RepositoryAdapter(project.repository_path)
        except RepositoryError as exc:
            raise CognitiveStateError(str(exc)) from exc
        commit_hash = self._git(repository, ["rev-parse", "--verify", f"{payload.commit_hash}^{{commit}}"])
        metadata = self._git(
            repository,
            ["show", "-s", "--format=%P%x00%D%x00%B", commit_hash],
        ).split("\x00", 2)
        parent_hash = metadata[0].split()[0] if metadata and metadata[0].strip() else None
        decorations = metadata[1].strip() if len(metadata) > 1 else ""
        message = metadata[2].strip() if len(metadata) > 2 else ""
        files = self._git_lines(
            repository,
            ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit_hash],
        )
        branches = self._git_lines(
            repository,
            ["branch", "--contains", commit_hash, "--format=%(refname:short)"],
        )
        return self.ingest_commit(
            project_id,
            CommitIngestV1(
                commit_hash=commit_hash,
                parent_hash=parent_hash,
                branch=branches[0] if branches else decorations or None,
                message=message,
                agent_id=payload.agent_id,
                trace_id=payload.trace_id,
                files=files,
                tests=[path for path in files if _TEST_PATH.search(path)],
                semantic_summary=payload.semantic_summary,
            ),
        )

    def handoff(
        self, project_id: str, payload: AgentHandoffCreateV1
    ) -> AgentHandoffV1:
        self._project(project_id)
        if payload.snapshot_id:
            snapshot = self.snapshot(project_id, snapshot_id=payload.snapshot_id)
            if snapshot is None:
                raise CognitiveStateNotFoundError("Cognitive snapshot not found.")
        event = self.record_event(
            project_id,
            CognitiveEventCreateV1(
                event_type="AGENT_HANDOFF",
                agent_id=payload.from_agent,
                snapshot_id=payload.snapshot_id,
                payload={
                    "from_agent": payload.from_agent,
                    "to_agent": payload.to_agent,
                    "requirement_ids": payload.requirement_ids,
                    "decision_ids": payload.decision_ids,
                    "constraints": payload.constraints,
                    "context_hash": payload.context_hash,
                },
            ),
            commit=False,
        )
        relationships = [
            ("AGENT", payload.from_agent, "AGENT", payload.to_agent, "HANDOFF_TO"),
            ("HANDOFF", event.id, "AGENT", payload.from_agent, "FROM_AGENT"),
            ("HANDOFF", event.id, "AGENT", payload.to_agent, "TO_AGENT"),
        ]
        if payload.snapshot_id:
            relationships.append(("HANDOFF", event.id, "SNAPSHOT", payload.snapshot_id, "REFERENCES"))
        relationships.extend(
            ("HANDOFF", event.id, "REQUIREMENT", requirement_id, "CARRIES_REQUIREMENT")
            for requirement_id in payload.requirement_ids
        )
        relationships.extend(
            ("HANDOFF", event.id, "DECISION", decision_id, "CARRIES_DECISION")
            for decision_id in payload.decision_ids
        )
        for source_type, source_id, target_type, target_id, relation in relationships:
            self.db.add(
                self._relation(
                    project_id,
                    CognitiveRelationCreateV1(
                        source_type=source_type,
                        source_id=source_id,
                        target_type=target_type,
                        target_id=target_id,
                        relation=relation,
                    ),
                    event,
                )
            )
        self.db.commit()
        return AgentHandoffV1(
            id=event.id,
            project_id=project_id,
            from_agent=payload.from_agent,
            to_agent=payload.to_agent,
            snapshot_id=payload.snapshot_id,
            requirement_ids=payload.requirement_ids,
            decision_ids=payload.decision_ids,
            constraints=payload.constraints,
            context_hash=payload.context_hash,
            created_at=event.created_at,
        )

    def record_delivery_completion(
        self,
        project_id: str,
        *,
        delivery_id: str,
        task_id: str,
        run_id: str,
        actor_id: str,
        requirement_ids: list[str],
        evidence: list[dict[str, Any]],
        pull_request: dict[str, str | None],
        traceability: dict[str, Any],
        source_revision: str | None = None,
    ) -> dict[str, Any]:
        """Anchor a completed delivery in the project cognitive history.

        The Jira/run data model is authoritative for delivery execution.  This
        projection makes that outcome explainable alongside commit-level
        provenance without guessing that a run's base revision is its output
        revision.  A commit is attached only when it was already indexed in
        this project's Git-backed cognitive history.
        """
        self._project(project_id)
        existing = next(
            (
                item
                for item in self._events(project_id, as_of=None, limit=10_000)
                if item.event_type == "DELIVERY_COMPLETED"
                and item.payload.get("delivery_id") == delivery_id
            ),
            None,
        )
        if existing is not None:
            snapshot = self._delivery_snapshot(project_id, delivery_id)
            return {
                "event": self._event_read(existing),
                "snapshot": self._snapshot_read(snapshot),
                "idempotent": True,
            }

        indexed_commit = None
        if source_revision:
            indexed = (
                self.db.query(CognitiveEvent)
                .filter(
                    CognitiveEvent.project_id == project_id,
                    CognitiveEvent.event_type == "COMMIT_CREATED",
                    CognitiveEvent.commit_hash == source_revision,
                )
                .first()
            )
            indexed_commit = source_revision if indexed is not None else None

        event = self.record_event(
            project_id,
            CognitiveEventCreateV1(
                event_type="DELIVERY_COMPLETED",
                agent_id=actor_id,
                commit_hash=indexed_commit,
                payload={
                    "delivery_id": delivery_id,
                    "task_id": task_id,
                    "run_id": run_id,
                    "requirement_ids": requirement_ids,
                    "evidence": evidence,
                    "pull_request": pull_request,
                    "traceability": traceability,
                    "source_revision": source_revision,
                },
            ),
            commit=False,
        )
        relations = [
            ("DELIVERY", delivery_id, "TASK", task_id, "DELIVERS"),
            ("DELIVERY", delivery_id, "RUN", run_id, "COMPLETES"),
            ("DELIVERY", delivery_id, "AGENT", actor_id, "CREATED_BY"),
        ]
        relations.extend(
            ("DELIVERY", delivery_id, "REQUIREMENT", requirement_id, "SATISFIES")
            for requirement_id in requirement_ids
        )
        relations.extend(
            ("DELIVERY", delivery_id, "EVIDENCE", str(item["id"]), "PROVEN_BY")
            for item in evidence
            if item.get("id")
        )
        if pull_request.get("url"):
            relations.append(
                ("DELIVERY", delivery_id, "PULL_REQUEST", pull_request["url"], "PROPOSES")
            )
        if indexed_commit:
            relations.append(("DELIVERY", delivery_id, "COMMIT", indexed_commit, "DELIVERS"))
        for source_type, source_id, target_type, target_id, relation in relations:
            self.db.add(
                self._relation(
                    project_id,
                    CognitiveRelationCreateV1(
                        source_type=source_type,
                        source_id=source_id,
                        target_type=target_type,
                        target_id=target_id,
                        relation=relation,
                        commit_hash=indexed_commit,
                    ),
                    event,
                )
            )
        self.db.commit()

        snapshot = self.create_snapshot(
            project_id,
            commit_hash=indexed_commit,
            snapshot_type="FEATURE",
            reason=f"delivery_completed:{delivery_id}",
        )
        # The snapshot is created after the delivery event so it includes the
        # outcome and all proof edges; the link itself remains an immutable
        # piece of provenance.
        self.add_relation(
            project_id,
            CognitiveRelationCreateV1(
                source_type="DELIVERY",
                source_id=delivery_id,
                target_type="SNAPSHOT",
                target_id=snapshot.id,
                relation="PART_OF",
                commit_hash=indexed_commit,
            ),
            actor_id=actor_id,
        )
        return {
            "event": self._event_read(event),
            "snapshot": self._snapshot_read(snapshot),
            "idempotent": False,
        }

    def delivery_passport(
        self, project_id: str, delivery_id: str
    ) -> CognitiveDeliveryPassportV1:
        """Return a human and machine-readable passport grounded in evidence."""
        self._project(project_id)
        event = next(
            (
                item
                for item in self._events(project_id, as_of=None, limit=10_000)
                if item.event_type == "DELIVERY_COMPLETED"
                and item.payload.get("delivery_id") == delivery_id
            ),
            None,
        )
        if event is None:
            raise CognitiveStateNotFoundError("Cognitive delivery not found.")
        snapshot = self._delivery_snapshot(project_id, delivery_id)
        relations = (
            self.db.query(CognitiveRelation)
            .filter(
                CognitiveRelation.project_id == project_id,
                CognitiveRelation.source_type == "DELIVERY",
                CognitiveRelation.source_id == delivery_id,
            )
            .order_by(CognitiveRelation.valid_from, CognitiveRelation.id)
            .all()
        )
        payload = event.payload
        return CognitiveDeliveryPassportV1(
            project_id=project_id,
            delivery_id=delivery_id,
            task_id=str(payload["task_id"]),
            run_id=str(payload["run_id"]),
            source_revision=payload.get("source_revision"),
            requirements=list(payload.get("requirement_ids", [])),
            evidence=list(payload.get("evidence", [])),
            pull_request=dict(payload.get("pull_request", {})),
            traceability=dict(payload.get("traceability", {})),
            event=self._event_read(event),
            snapshot=self._snapshot_read(snapshot),
            provenance=[self._relation_read(item) for item in relations],
        )

    def create_snapshot(
        self,
        project_id: str,
        *,
        commit_hash: str | None,
        snapshot_type: str,
        reason: str | None = None,
    ) -> CognitiveSnapshot:
        self._project(project_id)
        as_of = self._time_for_commit(project_id, commit_hash) if commit_hash else None
        state = self._state(project_id, as_of=as_of)
        state["commit_hash"] = commit_hash
        state["reason"] = reason
        snapshot = CognitiveSnapshot(
            id=str(uuid.uuid4()),
            project_id=project_id,
            commit_hash=commit_hash,
            snapshot_type=snapshot_type,
            state=state,
            state_hash=self._hash(state),
            created_at=_utcnow(),
        )
        self.db.add(snapshot)
        self.db.flush()
        self.record_event(
            project_id,
            CognitiveEventCreateV1(
                event_type="SNAPSHOT_CREATED",
                commit_hash=commit_hash,
                snapshot_id=snapshot.id,
                payload={
                    "snapshot_type": snapshot_type,
                    "state_hash": snapshot.state_hash,
                    "reason": reason,
                },
            ),
            commit=False,
        )
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    def snapshot(
        self,
        project_id: str,
        *,
        snapshot_id: str | None = None,
        commit_hash: str | None = None,
    ) -> CognitiveSnapshot | None:
        query = self.db.query(CognitiveSnapshot).filter(
            CognitiveSnapshot.project_id == project_id
        )
        if snapshot_id:
            return query.filter(CognitiveSnapshot.id == snapshot_id).first()
        if commit_hash:
            return (
                query.filter(CognitiveSnapshot.commit_hash == commit_hash)
                .order_by(CognitiveSnapshot.created_at.desc())
                .first()
            )
        return query.order_by(CognitiveSnapshot.created_at.desc()).first()

    def reconstruct_context(
        self,
        project_id: str,
        *,
        agent_id: str,
        task: str,
        commit_hash: str | None = None,
        snapshot_id: str | None = None,
        max_items: int = 20,
    ) -> CognitiveContextV1:
        snapshot = self.snapshot(
            project_id, snapshot_id=snapshot_id, commit_hash=commit_hash
        )
        if snapshot_id:
            as_of = (
                self._time_for_commit(project_id, snapshot.commit_hash)
                if snapshot and snapshot.commit_hash
                else (snapshot.created_at if snapshot else None)
            )
        elif commit_hash:
            as_of = self._time_for_commit(project_id, commit_hash)
        else:
            # A current reconstruction includes post-snapshot events such as
            # handoffs; callers that need time travel must specify a boundary.
            as_of = None
        relations = self._relations(project_id, as_of=as_of)
        state = self._state(project_id, as_of=as_of)
        memories = self._search_memories(project_id, task, as_of=as_of, limit=max_items)
        files = state["files"][:max_items]
        dependencies = [
            {"source": item.source_id, "target": item.target_id}
            for item in relations
            if item.relation == "DEPENDS_ON"
        ][:max_items]
        provenance = [
            {
                "event_id": item.id,
                "event_type": item.event_type,
                "commit_hash": item.commit_hash,
                "created_at": item.created_at.isoformat(),
            }
            for item in self._events(project_id, as_of=as_of, limit=max_items)
        ]
        handoffs = [
            {
                "id": item.id,
                **item.payload,
                "snapshot_id": item.snapshot_id,
                "created_at": item.created_at.isoformat(),
            }
            for item in self._events(project_id, as_of=as_of, limit=max_items)
            if item.event_type == "AGENT_HANDOFF"
        ]
        self.record_event(
            project_id,
            CognitiveEventCreateV1(
                event_type="CONTEXT_RECONSTRUCTED",
                agent_id=agent_id,
                commit_hash=commit_hash or (snapshot.commit_hash if snapshot else None),
                snapshot_id=snapshot.id if snapshot else None,
                payload={"task_hash": self._hash(task), "max_items": max_items},
            ),
        )
        return CognitiveContextV1(
            project_id=project_id,
            snapshot=self._snapshot_read(snapshot) if snapshot else None,
            task=task,
            requirements=state["requirements"][:max_items],
            decisions=state["decisions"][:max_items],
            skills=state["skills"][:max_items],
            code=files,
            dependencies=dependencies,
            tests=state["tests"][:max_items],
            agents=state["agents"][:max_items],
            memories=[self._memory_read(item) for item in memories],
            handoffs=handoffs,
            provenance=provenance,
        )

    def explain_file(
        self, project_id: str, path: str, *, commit_hash: str | None = None
    ) -> ExplainFileV1:
        path = self._safe_path(path)
        as_of = self._time_for_commit(project_id, commit_hash)
        relations = self._relations(project_id, as_of=as_of)
        file_edges = [
            edge
            for edge in relations
            if (edge.source_type == "FILE" and edge.source_id == path)
            or (edge.target_type == "FILE" and edge.target_id == path)
        ]
        commits = {
            edge.source_id if edge.source_type == "COMMIT" else edge.target_id
            for edge in file_edges
            if edge.source_type == "COMMIT" or edge.target_type == "COMMIT"
        }
        related = [
            edge
            for edge in relations
            if (edge.source_type == "COMMIT" and edge.source_id in commits)
            or (edge.target_type == "COMMIT" and edge.target_id in commits)
        ]
        values = self._values(related)
        return ExplainFileV1(
            project_id=project_id,
            file=path,
            requirements=values["REQUIREMENT"],
            decisions=values["DECISION"],
            commits=sorted(commits),
            agents=values["AGENT"],
            skills=values["SKILL"],
            dependencies=sorted(
                {
                    edge.target_id if edge.source_id == path else edge.source_id
                    for edge in file_edges
                    if edge.relation == "DEPENDS_ON"
                }
            ),
            tests=values["TEST"],
            evidence=[self._relation_read(edge) for edge in file_edges + related],
        )

    def impact_analysis(
        self, project_id: str, path: str, *, commit_hash: str | None = None
    ) -> ImpactAnalysisV1:
        path = self._safe_path(path)
        relations = self._relations(
            project_id, as_of=self._time_for_commit(project_id, commit_hash)
        )
        start = ("FILE", path)
        adjacent: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        evidence: dict[tuple[str, str], list[CognitiveRelation]] = defaultdict(list)
        for edge in relations:
            left, right = (edge.source_type, edge.source_id), (edge.target_type, edge.target_id)
            adjacent[left].add(right)
            adjacent[right].add(left)
            evidence[left].append(edge)
            evidence[right].append(edge)
        seen = {start}
        queue: deque[tuple[tuple[str, str], int]] = deque([(start, 0)])
        while queue:
            node, depth = queue.popleft()
            if depth == 3:
                continue
            for neighbor in adjacent[node]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, depth + 1))
        grouped: dict[str, list[str]] = defaultdict(list)
        for kind, identifier in seen:
            if (kind, identifier) != start:
                grouped[kind].append(identifier)
        return ImpactAnalysisV1(
            project_id=project_id,
            file=path,
            affected_files=sorted(set(grouped["FILE"])),
            affected_requirements=sorted(set(grouped["REQUIREMENT"])),
            affected_tests=sorted(set(grouped["TEST"])),
            affected_services=sorted(set(grouped["SERVICE"])),
            architectural_decisions=sorted(set(grouped["DECISION"])),
            evidence=[
                self._relation_read(item)
                for node in seen
                for item in evidence[node]
                if item.relation == "DEPENDS_ON"
            ],
        )

    def state_at_commit(self, project_id: str, commit_hash: str) -> dict[str, Any]:
        snapshot = self.snapshot(project_id, commit_hash=commit_hash)
        if snapshot is not None:
            return snapshot.state
        return self._state(project_id, as_of=self._time_for_commit(project_id, commit_hash))

    def _project(self, project_id: str) -> Project:
        project = self.db.get(Project, project_id)
        if project is None:
            raise CognitiveStateNotFoundError("Project not found.")
        return project

    def _delivery_snapshot(
        self, project_id: str, delivery_id: str
    ) -> CognitiveSnapshot | None:
        relation = (
            self.db.query(CognitiveRelation)
            .filter(
                CognitiveRelation.project_id == project_id,
                CognitiveRelation.source_type == "DELIVERY",
                CognitiveRelation.source_id == delivery_id,
                CognitiveRelation.target_type == "SNAPSHOT",
                CognitiveRelation.relation == "PART_OF",
            )
            .order_by(CognitiveRelation.valid_from.desc(), CognitiveRelation.id.desc())
            .first()
        )
        return self.snapshot(project_id, snapshot_id=relation.target_id) if relation else None

    def _lock_project(self, project_id: str) -> Project:
        """Serialize one project's hash chain when the backing store supports it."""
        query = self.db.query(Project).filter(Project.id == project_id)
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        project = query.first()
        if project is None:
            raise CognitiveStateNotFoundError("Project not found.")
        return project

    @staticmethod
    def _git(repository: RepositoryAdapter, arguments: list[str]) -> str:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=repository.repo_path,
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise CognitiveStateError("Unable to inspect the requested Git commit.") from exc
        return completed.stdout.strip()

    @classmethod
    def _git_lines(cls, repository: RepositoryAdapter, arguments: list[str]) -> list[str]:
        return [cls._safe_path(line) for line in cls._git(repository, arguments).splitlines() if line]

    def _relation(
        self, project_id: str, payload: CognitiveRelationCreateV1, event: CognitiveEvent) -> CognitiveRelation:
        return CognitiveRelation(
            id=str(uuid.uuid4()),
            project_id=project_id,
            source_type=payload.source_type.upper(),
            source_id=payload.source_id,
            target_type=payload.target_type.upper(),
            target_id=payload.target_id,
            relation=payload.relation.upper(),
            commit_hash=payload.commit_hash,
            metadata_=self._sanitize(payload.metadata),
            created_event_id=event.id,
            valid_from=event.created_at,
        )

    def _memory(
        self,
        project_id: str,
        *,
        content: str,
        memory_type: str,
        agent_id: str | None,
        commit_hash: str | None,
        importance: float,
        confidence: float,
        created_at: datetime | None = None,
    ) -> CognitiveMemory:
        safe_content = self._sanitize_text(content)
        return CognitiveMemory(
            id=str(uuid.uuid4()),
            project_id=project_id,
            memory_type=memory_type,
            content=safe_content,
            embedding=self._embed(safe_content),
            agent_id=agent_id,
            commit_hash=commit_hash,
            importance=importance,
            confidence=confidence,
            metadata_={"source": "commit_ingestion"},
            created_at=created_at or _utcnow(),
        )

    def _state(self, project_id: str, *, as_of: datetime | None) -> dict[str, Any]:
        values = self._values(self._relations(project_id, as_of=as_of))
        return {
            "requirements": values["REQUIREMENT"],
            "decisions": values["DECISION"],
            "agents": values["AGENT"],
            "skills": values["SKILL"],
            "files": values["FILE"],
            "tests": values["TEST"],
            "commits": values["COMMIT"],
            "services": values["SERVICE"],
            "generated_at": _utcnow().isoformat(),
        }

    def _relations(
        self, project_id: str, *, as_of: datetime | None = None
    ) -> list[CognitiveRelation]:
        query = self.db.query(CognitiveRelation).filter(
            CognitiveRelation.project_id == project_id
        )
        if as_of is not None:
            query = query.filter(CognitiveRelation.valid_from <= as_of)
        return query.order_by(CognitiveRelation.valid_from, CognitiveRelation.id).all()

    def _events(
        self, project_id: str, *, as_of: datetime | None, limit: int
    ) -> list[CognitiveEvent]:
        query = self.db.query(CognitiveEvent).filter(CognitiveEvent.project_id == project_id)
        if as_of is not None:
            query = query.filter(CognitiveEvent.created_at <= as_of)
        return query.order_by(CognitiveEvent.created_at.desc(), CognitiveEvent.id.desc()).limit(limit).all()

    def _time_for_commit(self, project_id: str, commit_hash: str | None) -> datetime | None:
        if not commit_hash:
            return None
        event = (
            self.db.query(CognitiveEvent)
            .filter(
                CognitiveEvent.project_id == project_id,
                CognitiveEvent.commit_hash == commit_hash,
                CognitiveEvent.event_type == "COMMIT_CREATED",
            )
            .order_by(CognitiveEvent.created_at.desc())
            .first()
        )
        if event is None:
            raise CognitiveStateNotFoundError(f"Commit {commit_hash} is not indexed.")
        return event.created_at

    def _search_memories(
        self, project_id: str, query: str, *, as_of: datetime | None, limit: int
    ) -> list[CognitiveMemory]:
        memories = self.db.query(CognitiveMemory).filter(CognitiveMemory.project_id == project_id)
        if as_of is not None:
            memories = memories.filter(CognitiveMemory.created_at <= as_of)
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            embedding = self._embed(self._sanitize_text(query))
            if embedding is not None:
                distance = CognitiveMemory.embedding.cosine_distance(embedding)
                return (
                    memories.filter(CognitiveMemory.embedding.is_not(None))
                    .order_by(distance, CognitiveMemory.importance.desc())
                    .limit(limit)
                    .all()
                )
        terms = set(word.lower() for word in _WORD.findall(query))
        candidates = memories.order_by(CognitiveMemory.created_at.desc()).all()
        return sorted(
            candidates,
            key=lambda item: (
                len(terms.intersection(word.lower() for word in _WORD.findall(item.content))),
                item.importance,
                item.confidence,
                item.created_at,
            ),
            reverse=True,
        )[:limit]

    @staticmethod
    def _values(relations: Iterable[CognitiveRelation]) -> dict[str, list[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        for relation in relations:
            result[relation.source_type].add(relation.source_id)
            result[relation.target_type].add(relation.target_id)
        values = {key: sorted(value) for key, value in result.items()}
        for kind in (
            "REQUIREMENT",
            "DECISION",
            "AGENT",
            "SKILL",
            "FILE",
            "TEST",
            "COMMIT",
            "SERVICE",
        ):
            values.setdefault(kind, [])
        return values

    @staticmethod
    def _safe_path(value: str) -> str:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise CognitiveStateError("Code paths must be project-relative.")
        return str(path)

    def _embed(self, content: str) -> list[float] | None:
        try:
            service = self.embedding_service
            if service is None:
                from sacm.ml.embeddings import EmbeddingService

                service = EmbeddingService()
                self.embedding_service = service
            return service.embed(content)
        except Exception:
            # The durable memory remains retrievable by lexical fallback and can
            # be embedded later; losing the commit event would be worse.
            return None

    @staticmethod
    def _message_metadata(message: str) -> dict[str, list[str]]:
        values: dict[str, list[str]] = defaultdict(list)
        for line in message.splitlines():
            match = _METADATA_LINE.match(line)
            if not match:
                continue
            key = match.group(1).replace(" ", "_").upper()
            values[key].extend(
                item.strip() for item in match.group(2).split(",") if item.strip()
            )
        return values

    @staticmethod
    def _merge_ids(*groups: list[str]) -> list[str]:
        return sorted({item.strip() for group in groups for item in group if item.strip()})

    @staticmethod
    def _first(values: list[str]) -> str | None:
        return values[0] if values else None

    @staticmethod
    def _summary(
        commit: str,
        requirements: list[str],
        decisions: list[str],
        files: list[str],
        tests: list[str],
        message: str,
    ) -> str:
        return "\n".join(
            [
                f"Commit: {commit}",
                f"Message: {message.splitlines()[0] if message else ''}",
                f"Requirements: {', '.join(requirements) or 'unknown'}",
                f"Decisions: {', '.join(decisions) or 'unknown'}",
                f"Files: {', '.join(files) or 'none'}",
                f"Tests: {', '.join(tests) or 'none'}",
            ]
        )

    @classmethod
    def _sanitize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): "[REDACTED]" if _SECRET_FIELD.search(str(key)) else cls._sanitize(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [cls._sanitize(item) for item in value]
        if isinstance(value, str):
            return cls._sanitize_text(value)
        return value

    @staticmethod
    def _sanitize_text(value: str) -> str:
        return _TOKEN_VALUE.sub("[REDACTED]", value)

    @staticmethod
    def _hash(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    @staticmethod
    def _relation_payload(payload: CognitiveRelationCreateV1) -> dict[str, Any]:
        return {
            "source_type": payload.source_type,
            "source_id": payload.source_id,
            "target_type": payload.target_type,
            "target_id": payload.target_id,
            "relation": payload.relation,
        }

    @staticmethod
    def _event_read(event: CognitiveEvent) -> CognitiveEventV1:
        return CognitiveEventV1(
            id=event.id,
            project_id=event.project_id,
            event_type=event.event_type,
            agent_id=event.agent_id,
            commit_hash=event.commit_hash,
            snapshot_id=event.snapshot_id,
            trace_id=event.trace_id,
            parent_event_id=event.parent_event_id,
            payload=event.payload,
            event_hash=event.event_hash,
            previous_event_hash=event.previous_event_hash,
            created_at=event.created_at,
        )

    @staticmethod
    def _relation_read(relation: CognitiveRelation) -> dict[str, Any]:
        return {
            "id": relation.id,
            "source_type": relation.source_type,
            "source_id": relation.source_id,
            "target_type": relation.target_type,
            "target_id": relation.target_id,
            "relation": relation.relation,
            "commit_hash": relation.commit_hash,
            "created_event_id": relation.created_event_id,
        }

    @staticmethod
    def _snapshot_read(snapshot: CognitiveSnapshot | None) -> CognitiveSnapshotV1 | None:
        if snapshot is None:
            return None
        return CognitiveSnapshotV1(
            id=snapshot.id,
            project_id=snapshot.project_id,
            commit_hash=snapshot.commit_hash,
            snapshot_type=snapshot.snapshot_type,
            state=snapshot.state,
            state_hash=snapshot.state_hash,
            created_at=snapshot.created_at,
        )

    @staticmethod
    def _memory_read(memory: CognitiveMemory) -> dict[str, Any]:
        return {
            "id": memory.id,
            "type": memory.memory_type,
            "content": memory.content,
            "commit_hash": memory.commit_hash,
            "importance": memory.importance,
            "confidence": memory.confidence,
            "created_at": memory.created_at.isoformat(),
        }
