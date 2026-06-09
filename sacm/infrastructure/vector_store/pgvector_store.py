from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import MemoryChunk


class PgVectorStore:
    def __init__(self, db: Session):
        self.db = db

    def upsert(self, chunk: MemoryChunk) -> MemoryChunk:
        self.db.add(chunk)
        self.db.commit()
        self.db.refresh(chunk)
        return chunk
