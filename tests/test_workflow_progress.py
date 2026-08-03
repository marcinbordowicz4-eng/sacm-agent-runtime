from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.event_service import EventService
from sacm.core.workflow_progress_service import WorkflowProgressService
from sacm.infrastructure.db.models import Base, Task, TaskRunLease
from sacm.infrastructure.db.session import get_db


def _task(db) -> Task:
    task = Task(
        id="task-progress",
        title="Live task",
        description="Exercise progress reporting",
        status="testing",
    )
    db.add(task)
    db.commit()
    return task


def test_progress_api_requires_auth_and_reports_active_workflow():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    task = _task(db)
    started = EventService(db).save(
        task.id,
        "workflow_progress",
        {
            "schema_version": "workflow-progress/v1",
            "task_id": task.id,
            "run_id": "run-1",
            "phase": "testing",
            "status": "started",
            "task_status": "testing",
            "agent": None,
            "step": 0,
            "elapsed_ms": 0,
        },
    )
    started.created_at = datetime.utcnow() - timedelta(seconds=2)
    db.add(started)
    EventService(db).save(
        task.id,
        "workflow_progress",
        {
            "schema_version": "workflow-progress/v1",
            "task_id": task.id,
            "run_id": "run-1",
            "phase": "testing",
            "status": "verification_started",
            "task_status": "testing",
            "agent": "CloudExecutor",
            "step": 2,
            "elapsed_ms": 1_000,
            "error": "must not be exposed",
        },
    )
    now = datetime.utcnow()
    db.add(
        TaskRunLease(
            task_id=task.id,
            owner_token="owner",
            acquired_at=now,
            heartbeat_at=now,
            expires_at=now + timedelta(minutes=1),
        )
    )
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            path = f"/v1/tasks/{task.id}/progress"
            assert client.get(path).status_code == 401
            response = client.get(path, headers={"X-SACM-Actor": "developer"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["state"] == "running"
        assert payload["lease_active"] is True
        assert payload["phase"] == "testing"
        assert payload["agent"] == "CloudExecutor"
        assert payload["step"] == 2
        assert payload["elapsed_ms"] >= 1_900
        assert len(payload["entries"]) == 2
        assert "error" not in payload["entries"][0]["details"]
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_progress_service_reports_stalled_finished_and_failed(db):
    task = _task(db)
    EventService(db).save(
        task.id,
        "workflow_progress",
        {
            "phase": "coding",
            "status": "agent_started",
            "elapsed_ms": 500,
        },
    )
    service = WorkflowProgressService(db)

    assert service.get(task.id).state == "stalled"

    task.status = "done"
    db.commit()
    assert service.get(task.id).state == "finished"

    task.status = "failed"
    db.commit()
    assert service.get(task.id).state == "failed"

    task.status = "cancelled"
    db.commit()
    assert service.get(task.id).state == "cancelled"
