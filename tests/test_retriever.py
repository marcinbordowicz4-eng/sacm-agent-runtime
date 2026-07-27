from unittest.mock import MagicMock

from sacm.core.retriever import MemoryRetriever


def test_memory_retriever_returns_langchain_documents():
    chunk = MagicMock(
        id="chunk-1",
        content="Relevant repository guidance",
        source_type="agent",
        source_id="event-1",
        importance=0.7,
    )
    memory_service = MagicMock()
    memory_service.search.return_value = [chunk]
    retriever = MemoryRetriever.for_task(memory_service, "task-1", top_k=3)

    documents = retriever.invoke("repository guidance")

    assert documents[0].page_content == "Relevant repository guidance"
    assert documents[0].metadata["memory_chunk_id"] == "chunk-1"
    memory_service.search.assert_called_once_with("task-1", "repository guidance", 3)
