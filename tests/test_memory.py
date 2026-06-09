from unittest.mock import patch

from sacm.core.memory_service import MemoryService
from sacm.infrastructure.db.models import Task


def test_add_memory(db):
    task = Task(id="task-1", title="Task", description="desc", status="pending")
    db.add(task)
    db.commit()
    service = MemoryService(db)
    with patch.object(service.embedding_service, "embed", return_value=[0.1] * 1536):
        chunk = service.add("task-1", "Test memory content", "test")
        assert chunk.content == "Test memory content"


def test_search_returns_list(db):
    service = MemoryService(db)
    result = service.search("task-999", "query", top_k=5)
    assert isinstance(result, list)
