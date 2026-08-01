from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.agent_registry import AgentRegistry
from sacm.core.outcome_router_service import OutcomeRouterService
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import AgentOutcomeAnalytics, Base, Organization
from sacm.infrastructure.db.session import get_db
from sacm.schemas.run import RunCreate


class FakeNeuralRouter:
    def __init__(self, preferred: str) -> None:
        self.preferred = preferred

    def route(self, context_vector, belief_state):
        names = AgentRegistry().names()
        probabilities = [0.01] * len(names)
        probabilities[names.index(self.preferred)] = 0.82
        total = sum(probabilities)
        probabilities = [value / total for value in probabilities]
        return {
            "selected_agent_index": names.index(self.preferred),
            "agent_probs": probabilities,
            "next_belief": belief_state,
        }


def _run(db, title="Fix Python API", description="Fix FastAPI regression"):
    return RunService(db).create(RunCreate(title=title, description=description))


def _outcome(db, run, agent_name, index, outcome, **values):
    row = AgentOutcomeAnalytics(
        id=f"{agent_name}-{index}",
        run_id=run.id,
        source_event_id=f"event-{agent_name}-{index}",
        agent_name=agent_name,
        role="coder",
        status="COMPLETED" if outcome == "success" else "FAILED",
        outcome=outcome,
        retry_count=values.get("retry_count", 0),
        estimated_cost_usd=values.get("cost", 1.0),
        latency_ms=values.get("latency", 1000),
        verification_count=values.get("verification_count", 1),
        test_count=values.get("test_count", 1),
        failure=values.get("failure"),
        details={},
        legacy_attribution=False,
        source_fingerprint=f"fingerprint-{agent_name}-{index}",
        computed_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()


def test_router_uses_neural_fallback_below_minimum_samples(db, monkeypatch):
    monkeypatch.setenv("SACM_ROUTER_MIN_SAMPLES", "3")
    run = _run(db)
    _outcome(db, run, "BackendAgent", 1, "success")
    service = OutcomeRouterService(
        db,
        neural_router=FakeNeuralRouter("CodexCoder"),
    )

    decision = service.rank(
        run.task,
        [0.0] * 256,
        [1.0 / 7] * 7,
        role="coder",
    )

    assert decision.strategy == "NEURAL_FALLBACK"
    assert decision.selected_agent_name == "CodexCoder"
    assert "not human acceptance" in decision.outcome_semantics


def test_retries_from_one_run_do_not_satisfy_sample_gate(db, monkeypatch):
    monkeypatch.setenv("SACM_ROUTER_MIN_SAMPLES", "3")
    run = _run(db)
    for index in range(3):
        _outcome(db, run, "BackendAgent", index, "success")
    service = OutcomeRouterService(
        db,
        neural_router=FakeNeuralRouter("CodexCoder"),
    )

    decision = service.rank(
        run.task,
        [0.0] * 256,
        [1.0 / 7] * 7,
        role="coder",
    )

    backend = next(
        item for item in decision.candidates if item.agent_name == "BackendAgent"
    )
    assert decision.strategy == "NEURAL_FALLBACK"
    assert backend.samples == 1
    assert backend.trusted_outcomes is False


def test_router_ignores_non_terminal_outcomes(db, monkeypatch):
    monkeypatch.setenv("SACM_ROUTER_MIN_SAMPLES", "3")
    for index in range(3):
        run = _run(db)
        _outcome(db, run, "BackendAgent", index, "cancelled")
    service = OutcomeRouterService(
        db,
        neural_router=FakeNeuralRouter("CodexCoder"),
    )

    decision = service.rank(
        run.task,
        [0.0] * 256,
        [1.0 / 7] * 7,
        role="coder",
    )

    backend = next(
        item for item in decision.candidates if item.agent_name == "BackendAgent"
    )
    assert decision.strategy == "NEURAL_FALLBACK"
    assert backend.samples == 0


def test_router_outcomes_are_isolated_by_organization(db, monkeypatch):
    monkeypatch.setenv("SACM_ROUTER_MIN_SAMPLES", "3")
    organization_a = Organization(slug="organization-a", name="Organization A")
    organization_b = Organization(slug="organization-b", name="Organization B")
    db.add_all([organization_a, organization_b])
    db.commit()
    tenant_run = _run(db)
    tenant_run.task.organization_id = organization_a.id
    db.commit()
    for index in range(3):
        other_run = _run(db)
        other_run.task.organization_id = organization_b.id
        db.commit()
        _outcome(db, other_run, "BackendAgent", index, "success")
    service = OutcomeRouterService(
        db,
        neural_router=FakeNeuralRouter("CodexCoder"),
    )

    decision = service.rank(
        tenant_run.task,
        [0.0] * 256,
        [1.0 / 7] * 7,
        role="coder",
    )

    backend = next(
        item for item in decision.candidates if item.agent_name == "BackendAgent"
    )
    assert decision.strategy == "NEURAL_FALLBACK"
    assert decision.selected_agent_name == "CodexCoder"
    assert backend.samples == 0


def test_router_prefers_trusted_successful_candidate(db, monkeypatch):
    monkeypatch.setenv("SACM_ROUTER_MIN_SAMPLES", "3")
    successful_runs = [_run(db) for _ in range(4)]
    failed_runs = [_run(db) for _ in range(4)]
    for index, run in enumerate(successful_runs):
        _outcome(db, run, "BackendAgent", index, "success", cost=2.0)
    for index, run in enumerate(failed_runs):
        _outcome(db, run, "CodexCoder", index, "failure", cost=1.0)
    service = OutcomeRouterService(
        db,
        neural_router=FakeNeuralRouter("CodexCoder"),
    )

    decision = service.rank(
        successful_runs[0].task,
        [0.0] * 256,
        [1.0 / 7] * 7,
        role="coder",
    )

    assert decision.strategy == "OUTCOME_ADAPTIVE"
    assert decision.selected_agent_name == "BackendAgent"
    candidate = next(
        item for item in decision.candidates if item.agent_name == "BackendAgent"
    )
    assert candidate.samples == 4
    assert candidate.trusted_outcomes is True
    assert candidate.data_scope in {"task_tags", "global"}


def test_router_penalizes_repeated_failure_pattern(db, monkeypatch):
    monkeypatch.setenv("SACM_ROUTER_MIN_SAMPLES", "1")
    run_a = _run(db)
    run_b = _run(db)
    _outcome(
        db,
        run_a,
        "BackendAgent",
        1,
        "success",
        failure={"classification": "COMPILATION"},
    )
    _outcome(db, run_b, "CodexCoder", 1, "success")
    service = OutcomeRouterService(
        db,
        neural_router=FakeNeuralRouter("BackendAgent"),
    )

    decision = service.rank(
        run_a.task,
        [0.0] * 256,
        [1.0 / 7] * 7,
        role="coder",
        previous_failure_classification="COMPILATION",
    )

    backend = next(
        item for item in decision.candidates if item.agent_name == "BackendAgent"
    )
    assert any("Repeated-failure penalty" in reason for reason in backend.reasons)


def test_authenticated_rank_api_returns_explainable_fallback():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = _run(db)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            assert (
                client.post(
                    "/v1/router/rank",
                    json={"task_id": run.task_id, "role": "coder"},
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/v1/router/route",
                    json={"task_id": run.task_id},
                ).status_code
                == 401
            )
            response = client.post(
                "/v1/router/rank",
                headers={"X-SACM-Actor": "developer"},
                json={"task_id": run.task_id, "role": "coder"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["strategy"] == "NEURAL_FALLBACK"
            assert body["candidates"]
            assert body["outcome_semantics"]
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
