from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.recovery_service import RecoveryService
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import Base
from sacm.infrastructure.db.session import get_db
from sacm.schemas.recovery import FailureClassification, RecoveryAction
from sacm.schemas.run import RunCreate


def _failed_step(db):
    runs = RunService(db)
    run = runs.create(RunCreate(title="Repair", description="Repair a failed change."))
    step = runs.add_step(run.id, "coder", {}, f"{run.id}:coder")
    runs.start_step(run.id, step.id)
    runs.fail_step(
        run.id,
        step.id,
        {"type": "SyntaxError", "message": "compile failed in checkout.py"},
    )
    return run, step


def test_failure_classifier_selects_specific_recovery_strategies(db):
    service = RecoveryService(db)

    compilation = service.classify(
        {"type": "SyntaxError", "message": "Compiler failed."}
    )
    context = service.classify(
        {"type": "AgentError", "message": "Cannot find definition; missing context."}
    )

    assert compilation.classification == FailureClassification.COMPILATION
    assert context.classification == FailureClassification.MISSING_CONTEXT


def test_recovery_schedules_bounded_code_repair_with_runtime_context(db):
    run, step = _failed_step(db)

    recovered, report, decision = RecoveryService(db).handle(
        run.id,
        step.id,
        {"type": "SyntaxError", "message": "compile failed in checkout.py"},
    )
    stored = RunService(db).get(run.id)

    assert report.classification == FailureClassification.COMPILATION
    assert decision.action == RecoveryAction.REPAIR_CODE
    assert recovered.status == "PENDING"
    assert recovered.retry_count == 1
    assert stored.status == "FIXING"
    assert stored.recovery_attempt_count == 1
    assert stored.recovery_state["last_decision"]["action"] == "REPAIR_CODE"
    assert [event.event_type for event in RunService(db).events(run.id)][-3:] == [
        "FailureClassified",
        "RecoveryPlanned",
        "RecoveryScheduled",
    ]


def test_recovery_escalates_after_attempt_budget(db, monkeypatch):
    monkeypatch.setenv("SACM_MAX_RECOVERY_ATTEMPTS", "0")
    run, step = _failed_step(db)

    recovered, _, decision = RecoveryService(db).handle(
        run.id,
        step.id,
        {"type": "ToolError", "message": "tool failed"},
    )
    stored = RunService(db).get(run.id)

    assert recovered.status == "FAILED"
    assert decision.action == RecoveryAction.ESCALATE
    assert stored.status == "FAILED"
    assert stored.recovery_state["status"] == "ESCALATED"


def test_recovery_api_exposes_diagnosis_and_decision():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run, step = _failed_step(db)
    RecoveryService(db).handle(
        run.id,
        step.id,
        {"type": "SyntaxError", "message": "compile failed in checkout.py"},
    )

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/v1/runs/{run.id}/recovery",
                headers={"X-SACM-Actor": "developer"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["last_failure"]["classification"] == "COMPILATION"
            assert body["last_decision"]["action"] == "REPAIR_CODE"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
