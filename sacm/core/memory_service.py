import uuid
import hashlib
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from sacm.core.tenancy_service import ResourceAuthorizationService
from sacm.infrastructure.db.models import MemoryChunk, Task
from sacm.ml.embeddings import EmbeddingService


class MemoryService:
    def __init__(self, db: Session, embedding_service: Optional[EmbeddingService] = None):
        self.db = db
        self.embedding_service = embedding_service or EmbeddingService()

    def add(
        self,
        task_id: str,
        content: str,
        source_type: str = "manual",
        importance: float = 0.5,
        actor_id: str | None = None,
        source_id: str | None = None,
        confidence: float = 0.7,
        scope: str = "task",
        valid_until: datetime | None = None,
        supersedes_id: str | None = None,
    ) -> MemoryChunk:
        task = self._authorize(task_id, actor_id, "tasks.write")
        if task is None:
            raise ValueError(f"Task {task_id} not found.")
        context = ResourceAuthorizationService(self.db).task_context(task)
        if not 0 <= importance <= 1 or not 0 <= confidence <= 1:
            raise ValueError("Memory importance and confidence must be between 0 and 1.")
        scope_key = self._scope_key(task, context, scope)
        normalized = re.sub(r"\s+", " ", content).strip()
        if not normalized:
            raise ValueError("Memory content must not be empty.")
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        existing = (
            self.db.query(MemoryChunk)
            .filter(
                MemoryChunk.scope == scope,
                MemoryChunk.scope_key == scope_key,
                MemoryChunk.content_hash == content_hash,
                MemoryChunk.superseded_at.is_(None),
                or_(
                    MemoryChunk.valid_until.is_(None),
                    MemoryChunk.valid_until > datetime.utcnow(),
                ),
            )
            .order_by(MemoryChunk.created_at.desc())
            .first()
        )
        if existing is not None:
            existing.importance = max(existing.importance, importance)
            existing.confidence = max(existing.confidence, confidence)
            if valid_until and (
                existing.valid_until is None or valid_until > existing.valid_until
            ):
                existing.valid_until = valid_until
            self.db.commit()
            self.db.refresh(existing)
            return existing
        embedding = self.embedding_service.embed(content)
        chunk = MemoryChunk(
            id=str(uuid.uuid4()),
            task_id=task_id,
            organization_id=context.organization_id if context else None,
            project_id=context.project_id if context else None,
            tenant_attribution=(
                {
                    "schema_version": "tenant-attribution/v1",
                    "source": context.source,
                }
                if context
                else None
            ),
            data_region=task.data_region,
            data_classification=task.data_classification,
            source_type=source_type,
            source_id=source_id,
            scope=scope,
            scope_key=scope_key,
            content=content,
            content_hash=content_hash,
            embedding=embedding,
            importance=importance,
            confidence=confidence,
            valid_until=valid_until,
            supersedes_id=supersedes_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        if supersedes_id:
            previous = self.db.get(MemoryChunk, supersedes_id)
            if previous is None:
                raise ValueError("Superseded memory chunk not found.")
            if previous.scope != scope or previous.scope_key != scope_key:
                raise ValueError("Memory may supersede only within the same scope.")
            previous.superseded_at = datetime.utcnow()
        self.db.add(chunk)
        self.db.commit()
        self.db.refresh(chunk)
        return chunk

    def search(
        self,
        task_id: str,
        query: str,
        top_k: int = 8,
        actor_id: str | None = None,
        scopes: list[str] | None = None,
    ) -> list[MemoryChunk]:
        task = self._authorize(task_id, actor_id, "tasks.read")
        if task is None:
            return []
        if top_k < 1:
            return []

        context = ResourceAuthorizationService(self.db).task_context(task)
        allowed = self._scope_filters(task, context, scopes)
        now = datetime.utcnow()
        chunks = self.db.query(MemoryChunk).filter(
            or_(*allowed),
            MemoryChunk.superseded_at.is_(None),
            or_(MemoryChunk.valid_until.is_(None), MemoryChunk.valid_until > now),
        )
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            query_embedding = self.embedding_service.embed(query)
            distance = MemoryChunk.embedding.cosine_distance(query_embedding)
            return (
                chunks.filter(MemoryChunk.embedding.is_not(None))
                .order_by(
                    distance,
                    MemoryChunk.confidence.desc(),
                    MemoryChunk.importance.desc(),
                    MemoryChunk.created_at.desc(),
                )
                .limit(top_k)
                .all()
            )

        # SQLite is used by the test suite and has no pgvector operators.
        return (
            chunks.order_by(
                MemoryChunk.confidence.desc(),
                MemoryChunk.importance.desc(),
                MemoryChunk.created_at.desc(),
            )
            .limit(top_k)
            .all()
        )

    def add_from_agent_result(self, task_id: str, result: Any) -> None:
        if result.memory_update:
            self.add(task_id, result.memory_update, source_type="agent", importance=0.7)

    @staticmethod
    def _scope_key(task: Task, context, scope: str) -> str:
        if scope == "task":
            return task.id
        if scope == "project" and context and context.project_id:
            return context.project_id
        if scope == "organization" and context and context.organization_id:
            return context.organization_id
        if scope == "repository" and task.target_repo_path:
            return str(task.target_repo_path)
        raise ValueError(f"Memory scope {scope!r} is unavailable for this task.")

    def _scope_filters(self, task: Task, context, scopes: list[str] | None):
        requested = set(scopes or ("task", "project", "repository", "organization"))
        filters = []
        for scope in requested:
            try:
                key = self._scope_key(task, context, scope)
            except ValueError:
                continue
            filters.append(
                and_(MemoryChunk.scope == scope, MemoryChunk.scope_key == key)
            )
        return filters or [
            and_(MemoryChunk.scope == "task", MemoryChunk.scope_key == task.id)
        ]

    def _authorize(self, task_id: str, actor_id: str | None, permission: str):
        resources = ResourceAuthorizationService(self.db)
        if actor_id is not None:
            return resources.require_task(task_id, actor_id, permission)
        if resources._production():
            raise PermissionError("Authenticated tenant context is required.")
        return self.db.get(Task, task_id)
