from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from sacm.core.memory_service import MemoryService


class MemoryRetriever(BaseRetriever):
    """LangChain retriever backed by SACM's task-scoped pgvector memory."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    memory_service: Any
    task_id: str
    top_k: int = 8

    @classmethod
    def for_task(
        cls,
        memory_service: MemoryService,
        task_id: str,
        top_k: int = 8,
    ) -> "MemoryRetriever":
        return cls(memory_service=memory_service, task_id=task_id, top_k=top_k)

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        del run_manager
        return [
            Document(
                page_content=chunk.content,
                metadata={
                    "memory_chunk_id": chunk.id,
                    "source_type": chunk.source_type,
                    "source_id": chunk.source_id,
                    "importance": chunk.importance,
                },
            )
            for chunk in self.memory_service.search(self.task_id, query, self.top_k)
        ]
