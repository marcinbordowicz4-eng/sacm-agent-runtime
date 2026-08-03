from unittest.mock import patch
from datetime import datetime, timedelta

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


def test_memory_deduplicates_active_content_and_keeps_highest_quality(db):
    task = Task(id="task-1", title="Task", description="desc", status="pending")
    db.add(task)
    db.commit()
    service = MemoryService(db)
    with patch.object(service.embedding_service, "embed", return_value=[0.1] * 1536):
        first = service.add(
            task.id, "Same   finding", importance=0.4, confidence=0.5
        )
        repeated = service.add(
            task.id, "Same finding", importance=0.9, confidence=0.8
        )

    assert repeated.id == first.id
    assert repeated.importance == 0.9
    assert repeated.confidence == 0.8


def test_memory_superseding_and_expiry_hide_stale_entries(db):
    task = Task(id="task-1", title="Task", description="desc", status="pending")
    db.add(task)
    db.commit()
    service = MemoryService(db)
    with patch.object(service.embedding_service, "embed", return_value=[0.1] * 1536):
        previous = service.add(task.id, "Old architecture decision")
        current = service.add(
            task.id,
            "New architecture decision",
            supersedes_id=previous.id,
        )
        service.add(
            task.id,
            "Expired fact",
            valid_until=datetime.utcnow() - timedelta(seconds=1),
        )

    results = service.search(task.id, "architecture", top_k=10)

    assert [chunk.id for chunk in results] == [current.id]
    assert db.get(type(previous), previous.id).superseded_at is not None


def test_repository_scope_is_available_to_other_tasks_for_same_repository(
    db, tmp_path
):
    first_task = Task(
        id="task-1",
        title="First",
        description="desc",
        status="pending",
        target_repo_path=str(tmp_path),
    )
    second_task = Task(
        id="task-2",
        title="Second",
        description="desc",
        status="pending",
        target_repo_path=str(tmp_path),
    )
    db.add_all([first_task, second_task])
    db.commit()
    service = MemoryService(db)
    with patch.object(service.embedding_service, "embed", return_value=[0.1] * 1536):
        shared = service.add(
            first_task.id,
            "Repository convention",
            scope="repository",
        )

    results = service.search(
        second_task.id,
        "convention",
        scopes=["repository"],
    )

    assert [chunk.id for chunk in results] == [shared.id]
