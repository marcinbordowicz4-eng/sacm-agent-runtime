from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.analytics_service import AnalyticsService
from sacm.core.event_service import EventService
from sacm.core.external_agent_service import ExternalAgentService
from sacm.core.run_service import RunService
from sacm.core.tenancy_service import TenancyService
from sacm.infrastructure.db.models import (
    AgentOutcomeAnalytics,
    Approval,
    Base,
    EvidencePack,
    RunOutcomeAnalytics,
    RunStepOutcomeAnalytics,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.contracts import (
    AgentResultV1,
    ArtifactReference,
    ExternalAgentResultSubmit,
    ExternalAgentStepCreate,
    UsageRecord,
)
from sacm.schemas.result import AgentResult
from sacm.schemas.run import RunCreate
from sacm.schemas.task import RepositoryReference, TaskContractV1


def _durable_run(db):
    tenancy = TenancyService(db)
    organization = tenancy.create_organization(
        "analytics-org", "Analytics Org", "owner"
    )
    project = tenancy.create_project(
        organization.id,
        "runtime",
        "Runtime",
        "owner",
        "example/runtime",
        "/repositories/runtime",
    )
    run = RunService(db).create(
        RunCreate(
            title="Measure checkout",
            description="Payment succeeds for a valid cart.",
            project_id=project.id,
            target_repo_path=project.repository_path,
        )
    )
    contract = TaskContractV1(
        connector_type="generic",
        external_id="analytics-task",
        title=run.task.title,
        description=run.task.description,
        acceptance_criteria=["Payment succeeds for a valid cart."],
        repositories=[RepositoryReference(path=".")],
        requested_by="platform",
    )
    run.task.contract_version = contract.schema_version
    run.task.connector_type = contract.connector_type
    run.task.external_id = contract.external_id
    run.task.task_contract = contract.model_dump(mode="json")
    run.task.readiness_score = 1.0
    run.task.readiness_details = {"ready": True}
    db.commit()

    scheduled = ExternalAgentService(db).schedule(
        run.id,
        ExternalAgentStepCreate(
            framework="copilot",
            agent_name="BackendAgent",
            idempotency_key="checkout",
            role="coder",
            objective="Payment succeeds for a valid cart.",
            acceptance_criteria=["Payment succeeds for a valid cart."],
            token_budget=1000,
            timeout_seconds=60,
        ),
    )
    RunService(db).start_step(run.id, scheduled.step.id)
    ExternalAgentService(db).submit(
        run.id,
        scheduled.step.id,
        ExternalAgentResultSubmit(
            result=AgentResultV1(
                run_id=run.id,
                step_id=scheduled.step.id,
                status="COMPLETED",
                summary="Checkout implemented and verified.",
                artifacts=[
                    ArtifactReference(
                        artifact_type="test_results_junit",
                        uri="file://test-results.xml",
                    )
                ],
                evidence=[
                    ArtifactReference(
                        artifact_type="verification",
                        metadata={"changed_files": ["checkout.py"]},
                    )
                ],
                usage=[
                    UsageRecord(
                        provider="example",
                        model="model-1",
                        input_tokens=120,
                        output_tokens=30,
                        estimated_cost_usd=0.0125,
                    )
                ],
                confidence=0.9,
            )
        ).result,
    )
    step = scheduled.step
    started = datetime(2026, 1, 1, 12, 0, 0)
    step.started_at = started
    step.completed_at = started + timedelta(seconds=3)
    step.retry_count = 2
    run.status = "COMPLETED"
    run.started_at = started
    run.completed_at = started + timedelta(seconds=8)
    db.add(
        EvidencePack(
            run_id=run.id,
            path="/evidence/run",
            manifest_hash="manifest-hash",
        )
    )
    db.add(
        Approval(
            run_id=run.id,
            action="deploy",
            status="APPROVED",
        )
    )
    db.commit()
    return organization, project, run


def test_run_analytics_metrics_and_recomputation_are_idempotent(db):
    _, _, run = _durable_run(db)
    service = AnalyticsService(db)

    first = service.recompute_run(run.id)
    second = service.recompute_run(run.id)

    assert first.outcome == "success"
    assert first.latency_ms == 8000
    assert first.retry_count == 2
    assert first.input_tokens == 120
    assert first.output_tokens == 30
    assert first.estimated_cost_usd == 0.0125
    assert first.evidence_pack_count == 1
    assert first.requirement_coverage_percent == 100.0
    assert first.evidence_coverage_percent == 100.0
    assert first.changed_file_count == 1
    assert first.test_count == 1
    assert first.verification_count == 1
    assert first.agents[0].agent_name == "BackendAgent"
    assert first.agents[0].provider == "example"
    assert first.steps[0].retry_count == 2
    assert second.computed_at == first.computed_at
    assert db.query(RunOutcomeAnalytics).count() == 1
    assert db.query(RunStepOutcomeAnalytics).count() == 1
    assert db.query(AgentOutcomeAnalytics).count() == 1


def test_legacy_analytics_use_explicit_nulls_and_ignore_runtime_actors(db):
    run = RunService(db).create(
        RunCreate(title="Legacy", description="No durable metadata.")
    )
    RunService(db).transition(run.id, "PLANNING", "RunStarted", actor="system")

    analytics = AnalyticsService(db).recompute_run(run.id)

    assert analytics.data_state == "legacy"
    assert analytics.legacy_data is True
    assert analytics.outcome is None
    assert analytics.latency_ms is None
    assert analytics.input_tokens is None
    assert analytics.estimated_cost_usd is None
    assert analytics.requirement_coverage_percent is None
    assert analytics.policy_blocked is None
    assert analytics.security_finding_count is None
    assert analytics.agents == []


def test_native_agent_provenance_does_not_require_token_usage(db):
    run = RunService(db).create(
        RunCreate(title="Delivery preflight", description="Check GitHub delivery.")
    )
    EventService(db).save_agent_result(
        run.task_id,
        "GitHubDelivery",
        AgentResult(
            agent_name="GitHubDelivery",
            summary="GitHub CLI authentication is ready.",
            confidence=1.0,
            next_state_hint="reviewing",
        ),
    )

    analytics = AnalyticsService(db).recompute_run(run.id)

    assert analytics.agents[0].provider == "sacm"
    assert analytics.agents[0].model == "deterministic"
    assert analytics.agents[0].framework == "native"
    assert analytics.agents[0].input_tokens is None
    assert analytics.agents[0].output_tokens is None


def test_failure_and_cancelled_outcomes_are_classified(db):
    started = datetime(2026, 1, 1, 12, 0, 0)
    outcomes = {}
    for status in ("FAILED", "CANCELLED"):
        run = RunService(db).create(
            RunCreate(title=status, description=f"A {status.lower()} run.")
        )
        run.status = status
        run.started_at = started
        run.completed_at = started + timedelta(seconds=1)
        db.commit()
        outcomes[status] = AnalyticsService(db).recompute_run(run.id).outcome

    assert outcomes == {"FAILED": "failure", "CANCELLED": "cancelled"}


def test_analytics_aggregates_and_api_enforce_auth_and_tenancy():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    organization, project, run = _durable_run(db)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            run_path = f"/v1/runs/{run.id}/analytics"
            assert client.get(run_path).status_code == 401
            assert (
                client.get(run_path, headers={"X-SACM-Actor": "stranger"}).status_code
                == 403
            )
            run_response = client.get(run_path, headers={"X-SACM-Actor": "owner"})
            assert run_response.status_code == 200
            assert run_response.json()["outcome"] == "success"

            for path in (
                f"/v1/analytics/tasks/{run.task_id}",
                f"/v1/analytics/projects/{project.id}",
                f"/v1/analytics/organizations/{organization.id}",
            ):
                response = client.get(path, headers={"X-SACM-Actor": "owner"})
                assert response.status_code == 200
                body = response.json()
                assert body["run_count"] == 1
                assert body["success_count"] == 1
                assert body["success_rate_percent"] == 100.0
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
