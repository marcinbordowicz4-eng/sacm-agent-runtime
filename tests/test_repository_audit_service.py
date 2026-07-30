from sacm.core.event_service import EventService
from sacm.core.memory_service import MemoryService
from sacm.core.repository_audit_service import RepositoryAuditService
from sacm.core.task_service import TaskService
from sacm.infrastructure.db.models import ContextEvent, MemoryChunk
from sacm.schemas.task import TaskCreate


def test_repository_operation_persists_event_and_memory(db):
    task = TaskService(db).create(
        TaskCreate(title="Implement change", description="Persist implementation context.")
    )

    RepositoryAuditService(db).record(
        task.id,
        "verification_completed",
        "/repository",
        {"command": "pytest", "returncode": 0, "passed": True},
        memory_summary="Verification `pytest` passed with exit code 0.",
    )

    event = EventService(db).get_recent_events(task.id)[0]
    memory = MemoryService(db).search(task.id, "pytest", top_k=5)
    assert event.event_type == "repository_verification_completed"
    assert event.payload["passed"] is True
    assert memory[0].content == "Verification `pytest` passed with exit code 0."


def test_repository_operation_without_task_is_not_persisted(db):
    RepositoryAuditService(db).record(
        None,
        "diff_captured",
        "/repository",
        {"sha256": "abc"},
        memory_summary="Captured a diff.",
    )

    assert db.query(ContextEvent).count() == 0
    assert db.query(MemoryChunk).count() == 0
