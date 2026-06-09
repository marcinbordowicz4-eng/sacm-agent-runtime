import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import MemoryChunk
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
    ) -> MemoryChunk:
        embedding = self.embedding_service.embed(content)
        chunk = MemoryChunk(
            id=str(uuid.uuid4()),
            task_id=task_id,
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

    def search(self, task_id: str, query: str, top_k: int = 8) -> list[MemoryChunk]:
        del query
        return (
            self.db.query(MemoryChunk)
            .filter(MemoryChunk.task_id == task_id)
            .order_by(MemoryChunk.importance.desc(), MemoryChunk.created_at.desc())
            .limit(top_k)
            .all()
        )

    def add_from_agent_result(self, task_id: str, result: Any) -> None:
        if result.memory_update:
            self.add(task_id, result.memory_update, source_type="agent", importance=0.7)
