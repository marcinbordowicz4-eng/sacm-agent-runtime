from datetime import datetime, timedelta
from types import MethodType
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sacm.agents.mlflow_experiment_agent import MLflowExperimentAgent
from sacm.agents.otel_cost_agent import OpenTelemetryCostAgent
from sacm.core.event_service import EventService
from sacm.core.task_run_lease_service import TaskRunLeaseService
from sacm.core.task_service import TaskService
from sacm.infrastructure.db.models import Base, Task, TaskRunLease
from sacm.schemas.context import AgentContext


def _context() -> AgentContext:
    return AgentContext(
        task_id="task-1",
        task="Analyze costs",
        goal="Analyze task costs",
        current_state="planning",
    )


def test_orchestrator_imports():
    from sacm.core.orchestrator import Orchestrator

    assert Orchestrator is not None


def test_orchestrator_only_initializes_pending_tasks_to_planning():
    from sacm.core.orchestrator import Orchestrator

    assert Orchestrator._should_initialize_planning("pending") is True
    assert Orchestrator._should_initialize_planning("reviewing") is False
    assert Orchestrator._should_initialize_planning("testing") is False


def test_orchestrator_uses_deterministic_agents_for_workflow_phases():
    from sacm.core.orchestrator import Orchestrator

    assert Orchestrator._phase_agent_name("coding") == "CodexExecutor"
    assert Orchestrator._phase_agent_name("testing") == "CloudExecutor"
    assert Orchestrator._phase_agent_name("reviewing") == "Reviewer"


def test_orchestrator_does_not_accept_unverified_done_hint():
    from sacm.core.orchestrator import Orchestrator

    assert Orchestrator._unverified_next_state("done") == "testing"
    assert Orchestrator._unverified_next_state("reviewing") == "reviewing"


def _task(db, *, status="planning"):
    task = Task(
        id="task-1",
        title="Test task",
        description="Test task",
        status=status,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    db.commit()
    return task


def test_task_run_lease_acquire_conflict_expiry_and_release(db):
    _task(db)
    service = TaskRunLeaseService(db, lease_seconds=30)
    started = datetime(2026, 8, 2, 20, 0, 0)

    owner = service.acquire("task-1", now=started)
    with pytest.raises(RuntimeError, match="active orchestrator run"):
        service.acquire("task-1", now=started + timedelta(seconds=10))

    replacement = service.acquire(
        "task-1", now=started + timedelta(seconds=31)
    )
    assert replacement != owner
    assert service.release("task-1", owner) is False
    assert service.release("task-1", replacement) is True
    assert service.acquire("task-1", now=started + timedelta(seconds=32))


def test_task_run_lease_conflicts_across_database_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'leases.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    first = sessions()
    second = sessions()
    try:
        _task(first)
        owner = TaskRunLeaseService(first).acquire("task-1")

        with pytest.raises(RuntimeError, match="active orchestrator run"):
            TaskRunLeaseService(second).acquire("task-1")

        assert TaskRunLeaseService(first).release("task-1", owner) is True
    finally:
        first.close()
        second.close()
        engine.dispose()


def test_orchestrator_records_progress_refreshes_and_releases_lease(db):
    from sacm.core.orchestrator import Orchestrator

    _task(db)
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.db = db
    orchestrator.task_service = TaskService(db)
    orchestrator.event_service = EventService(db)

    def fake_locked(
        self,
        task_id,
        *,
        lease_service,
        owner_token,
        **kwargs,
    ):
        lease_service.heartbeat(task_id, owner_token)
        lease = db.get(TaskRunLease, task_id)
        assert lease.heartbeat_at >= lease.acquired_at
        return {"task_id": task_id, "status": "planning", "steps": 1}

    orchestrator._run_task_locked = MethodType(fake_locked, orchestrator)

    result = orchestrator.run_task("task-1")

    assert result["status"] == "planning"
    assert db.get(TaskRunLease, "task-1") is None
    progress = [
        event.payload
        for event in reversed(EventService(db).get_recent_events("task-1"))
        if event.event_type == "workflow_progress"
    ]
    assert [event["status"] for event in progress] == ["started", "finished"]
    assert all(event["task_id"] == "task-1" for event in progress)
    assert all("elapsed_ms" in event for event in progress)


def test_orchestrator_releases_lease_and_records_failure(db):
    from sacm.core.orchestrator import Orchestrator

    _task(db)
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.db = db
    orchestrator.task_service = TaskService(db)
    orchestrator.event_service = EventService(db)

    def fail_locked(self, task_id, **kwargs):
        raise RuntimeError("executor failed")

    orchestrator._run_task_locked = MethodType(fail_locked, orchestrator)

    with pytest.raises(RuntimeError, match="executor failed"):
        orchestrator.run_task("task-1")

    assert db.get(TaskRunLease, "task-1") is None
    progress = [
        event.payload
        for event in reversed(EventService(db).get_recent_events("task-1"))
        if event.event_type == "workflow_progress"
    ]
    assert [event["status"] for event in progress] == ["started", "failed"]
    assert progress[-1]["error_type"] == "RuntimeError"


def test_orchestrator_rejects_competing_run(db):
    from sacm.core.orchestrator import Orchestrator

    _task(db)
    owner = TaskRunLeaseService(db).acquire("task-1")
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.db = db
    orchestrator.task_service = TaskService(db)

    with pytest.raises(RuntimeError, match="active orchestrator run"):
        orchestrator.run_task("task-1")

    TaskRunLeaseService(db).release("task-1", owner)


def test_orchestrator_repairs_false_done_when_no_verification_completed(db):
    from sacm.core.orchestrator import Orchestrator

    _task(db, status="done")
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.db = db
    orchestrator.task_service = TaskService(db)
    orchestrator.event_service = EventService(db)
    trace = MagicMock()
    orchestrator.observability = MagicMock()
    orchestrator.observability.start_task.return_value = trace
    leases = TaskRunLeaseService(db)
    owner = leases.acquire("task-1")

    result = orchestrator._run_task_locked(
        "task-1",
        max_steps=0,
        run_id=None,
        recovery_context=None,
        lease_service=leases,
        owner_token=owner,
        started_at=0.0,
    )

    assert result["status"] == "testing"
    leases.release("task-1", owner)


def test_task_status_optimistic_update_preserves_newer_phase(db):
    task = _task(db, status="testing")
    original_updated_at = task.updated_at
    TaskService(db).update_status("task-1", "reviewing")

    stale_update = TaskService(db).update_status(
        "task-1",
        "debugging",
        expected_status="testing",
        expected_updated_at=original_updated_at,
    )

    assert stale_update is False
    assert TaskService(db).get("task-1").status == "reviewing"


def test_agent_registry_has_agents():
    from sacm.core.agent_registry import AgentRegistry

    registry = AgentRegistry()
    assert len(registry.all()) == 19
    assert "ClaudeReasoner" in registry.names()
    assert "OpenTelemetryCost" in registry.names()
    assert "MLflowExperiment" in registry.names()
    assert "CodexExecutor" in registry.names()
    assert "GitHubDelivery" in registry.names()
    assert "EASWorkflow" in registry.names()
    assert "MobileE2E" in registry.names()
    assert "SecurityDelivery" in registry.names()
    assert "OpenAIAgentsExecutor" in registry.names()


def test_cost_agent_identifies_missing_telemetry_configuration(monkeypatch):
    monkeypatch.delenv("SACM_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv(
        "SACM_OPENAI_EMBEDDING_INPUT_COST_PER_MILLION_USD", raising=False
    )

    result = OpenTelemetryCostAgent().run(_context())

    assert result.actions == [
        {
            "type": "COST_TELEMETRY",
            "otel_enabled": False,
            "collector_configured": False,
            "pricing_configured": False,
        }
    ]
    assert "cannot estimate costs" in result.summary


def test_mlflow_agent_does_not_claim_to_log_when_disabled(monkeypatch):
    monkeypatch.setenv("SACM_MLFLOW_ENABLED", "false")

    result = MLflowExperimentAgent().run(_context())

    assert result.actions == [{"type": "MLFLOW_EXPERIMENT", "logged": False}]
    assert [skill["skill_name"] for skill in result.skills_contributed] == [
        "router_experiment_assessed"
    ]


def test_verifier_done_on_high_confidence():
    from sacm.core.verifier import Verifier
    from sacm.schemas.result import AgentResult

    task = MagicMock()
    result = AgentResult(
        agent_name="test",
        summary="done",
        confidence=0.99,
        next_state_hint="reviewing",
        actions=[{"type": "TEST_RESULT", "passed": True}],
    )
    verifier = Verifier()
    assert verifier.is_done(task, result) is True


def test_verifier_not_done_when_blocked():
    from sacm.core.verifier import Verifier
    from sacm.schemas.result import AgentResult

    task = MagicMock()
    result = AgentResult(
        agent_name="test",
        summary="blocked",
        confidence=0.99,
        next_state_hint="blocked",
    )
    verifier = Verifier()
    assert verifier.is_done(task, result) is False
