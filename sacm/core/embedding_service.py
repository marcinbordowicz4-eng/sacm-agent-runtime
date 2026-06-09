from sacm.infrastructure.db.models import ContextEvent, MemoryChunk, Task
from sacm.ml.embeddings import EmbeddingService as BaseEmbeddingService

CONTEXT_DIM = 256


class EmbeddingService:
    def __init__(self):
        self._base = BaseEmbeddingService(dim=CONTEXT_DIM)

    def embed_task_context(
        self,
        task: Task,
        history: list[ContextEvent],
        memory: list[MemoryChunk],
    ) -> list[float]:
        summary_parts = [task.description]
        for event in history[:3]:
            if "summary" in event.payload:
                summary_parts.append(event.payload["summary"])
        for chunk in memory[:3]:
            summary_parts.append(chunk.content[:200])
        combined = " ".join(summary_parts)[:1000]
        return self._base.embed(combined)
