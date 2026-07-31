import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.application_context_service import ApplicationContextService
from sacm.core.execution_planning_service import (
    ApplicationContextRequiredError,
    DefinitionOfReadyError,
    ExecutionPlanningService,
)
from sacm.core.task_intake_service import TaskIntakeService
from sacm.infrastructure.db.models import (
    Base,
    ExecutionPlan,
    ExecutionPlanSecretRequirement,
    ExecutionPlanStep,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.execution_plan import AgentConfigurationV1
from sacm.schemas.task import RepositoryReference, TaskContractV1


def _ready_task(db, repository, *, metadata=None, external_id="execution-plan"):
    contract = TaskContractV1(
        connector_type="generic",
        external_id=external_id,
        title="Deploy secure order schema changes",
        description=(
            "Update the orders API and database schema. "
            "Deploy the compatible change to production."
        ),
        acceptance_criteria=[
            "POST /orders persists the new field.",
            "The database migration is reversible.",
        ],
        repositories=[RepositoryReference(path=str(repository))],
        requested_by="platform-owner",
        metadata=metadata or {},
    )
    return TaskIntakeService(db).ingest(contract)[0]


def _repository(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "models.py").write_text(
        """
class Order:
    __tablename__ = "orders"
""".strip()
    )
    (repository / "api.py").write_text(
        """
@router.post("/orders")
def create_order():
    return {}
""".strip()
    )
    return repository


def test_builds_deterministic_durable_gated_plan(db, tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    task = _ready_task(db, repository)
    ApplicationContextService(db).build(task.id)

    first = ExecutionPlanningService(db).build(task.id)
    second = ExecutionPlanningService(db).build(task.id)

    assert first.id == second.id
    assert first.steps == second.steps
    assert first.revision == 1
    assert first.status == "GATED"
    assert first.schema_version == "execution-plan/v1"
    assert first.risk_decision.schema_version == "risk-decision/v1"
    assert first.policy_decision.schema_version == "policy-decision/v1"
    assert first.security_review.status == "PENDING"
    assert first.security_review.reviewer.role == "security"
    assert first.approval_gates
    assert [step.sequence for step in first.steps] == list(
        range(1, len(first.steps) + 1)
    )
    assert first.steps[-2].kind == "verification"
    assert first.steps[-1].kind == "security_review"
    assert all(
        step.agent.task_contract == "agent-task/v1"
        and step.agent.result_contract == "agent-result/v1"
        and step.agent.implementation_ref.startswith("registry://")
        for step in first.steps
    )
    assert any("schema" in step.risk_tags for step in first.steps)
    assert any("deployment" in step.risk_tags for step in first.steps)
    assert db.query(ExecutionPlan).count() == 1
    assert db.query(ExecutionPlanStep).count() == len(first.steps)


def test_requires_definition_of_ready_and_application_context(
    db, tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    ready = _ready_task(db, repository, external_id="missing-context")

    with pytest.raises(ApplicationContextRequiredError):
        ExecutionPlanningService(db).build(ready.id)

    incomplete, _, _ = TaskIntakeService(db).ingest(
        TaskContractV1(
            connector_type="generic",
            external_id="not-ready",
            title="Incomplete task",
            description="",
            repositories=[RepositoryReference(path=str(repository))],
        )
    )
    ApplicationContextService(db).build(incomplete.id)

    with pytest.raises(DefinitionOfReadyError):
        ExecutionPlanningService(db).build(incomplete.id)


def test_secret_broker_never_persists_or_serializes_values(
    db, tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    secret_value = "never-persist-this-secret-value"
    monkeypatch.setenv("ORDERS_DEPLOY_TOKEN", secret_value)
    task = _ready_task(
        db,
        repository,
        external_id="secret-plan",
        metadata={
            "secret_requests": [
                {
                    "schema_version": "secret-request/v1",
                    "name": "orders-deploy-token",
                    "purpose": "Authenticate the deployment tool.",
                    "environment_variable": "ORDERS_DEPLOY_TOKEN",
                    "required": True,
                }
            ]
        },
    )
    ApplicationContextService(db).build(task.id)

    plan = ExecutionPlanningService(db).build(task.id, policy_pack="strict")
    persisted = db.query(ExecutionPlanSecretRequirement).one()
    serialized = json.dumps(plan.model_dump(mode="json"), sort_keys=True)
    database_payload = json.dumps(
        {"request": persisted.request, "reference": persisted.reference},
        sort_keys=True,
    )

    assert secret_value not in serialized
    assert secret_value not in database_payload
    assert plan.secret_references[0].available is True
    assert plan.secret_references[0].handle.startswith("secret-ref:")
    assert "value" not in plan.secret_references[0].metadata
    assert plan.policy_decision.pack == "strict"
    assert plan.policy_decision.requires_approval is True


def test_portable_agent_configuration_supports_external_adapters():
    configuration = AgentConfigurationV1(
        runtime_kind="external",
        agent_name="company-reviewer",
        role="reviewer",
        implementation_ref="adapter://company/reviewer",
        capabilities=["review"],
        configuration={"endpoint_reference": "review-service"},
    )

    assert configuration.task_contract == "agent-task/v1"
    assert configuration.result_contract == "agent-result/v1"
    assert "provider" not in configuration.model_dump()
    assert "model" not in configuration.model_dump()


def test_execution_plan_api_requires_auth_and_exposes_inspection_endpoints(
    tmp_path, monkeypatch
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    repository = _repository(tmp_path)
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    api_secret = "api-secret-must-not-leak"
    monkeypatch.setenv("ORDERS_API_TOKEN", api_secret)
    task = _ready_task(
        db,
        repository,
        external_id="execution-plan-api",
        metadata={
            "secret_requests": [
                {
                    "schema_version": "secret-request/v1",
                    "name": "orders-api-token",
                    "purpose": "Authenticate an external order API.",
                    "environment_variable": "ORDERS_API_TOKEN",
                }
            ]
        },
    )
    ApplicationContextService(db).build(task.id)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            path = f"/v1/tasks/{task.id}/execution-plan"
            assert client.post(path).status_code == 401
            headers = {"X-SACM-Actor": "planner"}
            response = client.post(path, headers=headers)
            assert response.status_code == 201
            assert api_secret not in response.text
            assert client.get(path, headers=headers).status_code == 200
            assert client.get(f"{path}/policy", headers=headers).status_code == 200
            assert (
                client.get(f"{path}/security-review", headers=headers).status_code
                == 200
            )
            secrets = client.get(
                f"{path}/secret-requirements", headers=headers
            )
            assert secrets.status_code == 200
            assert api_secret not in secrets.text
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
