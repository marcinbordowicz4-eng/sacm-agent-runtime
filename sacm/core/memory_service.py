import uuid
from datetime import datetime
from typing import Any, Optional

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
    ) -> MemoryChunk:
        task = self._authorize(task_id, actor_id, "tasks.write")
        context = ResourceAuthorizationService(self.db).task_context(task)
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
            content=content,
            embedding=embedding,
            importance=importance,
            created_at=datetime.utcnow(),
        )
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
    ) -> list[MemoryChunk]:
        self._authorize(task_id, actor_id, "tasks.read")
        if top_k < 1:
            return []

        chunks = self.db.query(MemoryChunk).filter(MemoryChunk.task_id == task_id)
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            query_embedding = self.embedding_service.embed(query)
            distance = MemoryChunk.embedding.cosine_distance(query_embedding)
            return (
                chunks.filter(MemoryChunk.embedding.is_not(None))
                .order_by(distance, MemoryChunk.importance.desc(), MemoryChunk.created_at.desc())
                .limit(top_k)
                .all()
            )

        # SQLite is used by the test suite and has no pgvector operators.
        return (
            chunks.order_by(MemoryChunk.importance.desc(), MemoryChunk.created_at.desc())
            .limit(top_k)
            .all()
        )

    def add_from_agent_result(self, task_id: str, result: Any) -> None:
        if result.memory_update:
            self.add(task_id, result.memory_update, source_type="agent", importance=0.7)

    def _authorize(self, task_id: str, actor_id: str | None, permission: str):
        resources = ResourceAuthorizationService(self.db)
        if actor_id is not None:
            return resources.require_task(task_id, actor_id, permission)
        if resources._production():
            raise PermissionError("Authenticated tenant context is required.")
        return self.db.get(Task, task_id)
