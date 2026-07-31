import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.bdd_traceability import BddTraceabilityService
from sacm.core.evidence_service import EvidenceService
from sacm.core.repository_audit_service import RepositoryAuditService
from sacm.core.run_service import RunService
from sacm.core.task_intake_service import TaskIntakeService
from sacm.core.traceability_service import TraceabilityService
from sacm.infrastructure.db.models import (
    ApplicationContext,
    Base,
    ExecutionPlan,
    ExecutionPlanStep,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.run import RunCreate
from sacm.schemas.task import RepositoryReference, TaskContractV1
from sacm.schemas.traceability import RequirementLinkCreateV1


def _contract(external_id: str = "traceability-task") -> TaskContractV1:
    return TaskContractV1(
        connector_type="generic",
        external_id=external_id,
        title="Trace checkout requirements",
        description="Feature: Checkout\nScenario: Pay\nGiven a cart\nThen payment succeeds",
        acceptance_criteria=[
            "Payment succeeds for a valid cart.",
            "A receipt is recorded.",
        ],
        repositories=[RepositoryReference(path=".")],
        requested_by="platform",
    )


def test_requirement_ids_are_stable_and_include_bdd_events(db):
    task, _, _ = TaskIntakeService(db).ingest(_contract())
    first = TraceabilityService(db).refresh(task.id)
    contract = dict(task.task_contract or {})
    contract["acceptance_criteria"] = [
        "  PAYMENT succeeds for a valid cart  ",
        "A receipt is recorded",
    ]
    task.task_contract = contract
    db.commit()
    normalized = TraceabilityService(db).refresh(task.id)
    BddTraceabilityService(db).register(task, "SHOP-42")
    second = TraceabilityService(db).refresh(task.id)
    third = TraceabilityService(db).refresh(task.id)

    assert [item.id for item in second.requirements] == [
        item.id for item in third.requirements
    ]
    assert [item.id for item in first.requirements] == [
        item.id for item in normalized.requirements
    ]
    assert [item.id for item in normalized.requirements] == [
        item.id for item in second.requirements[:2]
    ]
    assert all(item.schema_version == "requirement/v1" for item in second.requirements)
    assert any(
        source["source_type"] == "bdd_event"
        for requirement in second.requirements
        for source in requirement.source_refs
    )


def test_coverage_and_cross_links_are_deterministic(db):
    task, _, _ = TaskIntakeService(db).ingest(_contract("coverage"))
    initial = TraceabilityService(db).refresh(task.id)
    assert initial.coverage.covered_requirements == 0
    assert len(initial.coverage.uncovered_requirements) == 2

    context = ApplicationContext(
        task_id=task.id,
        status="complete",
        graph={},
        graph_hash="graph-hash",
        impact_analysis={},
        risk_analysis={},
    )
    db.add(context)
    db.flush()
    plan = ExecutionPlan(
        task_id=task.id,
        application_context_id=context.id,
        revision=1,
        source_hash="plan-hash",
        status="READY",
        policy_pack="default",
    )
    db.add(plan)
    db.flush()
    db.add(
        ExecutionPlanStep(
            execution_plan_id=plan.id,
            sequence=1,
            stable_key="payment",
            kind="implementation",
            title="Payment",
            objective="Payment succeeds for a valid cart.",
            acceptance_criteria=["Payment succeeds for a valid cart."],
            context_references=[],
            impacted_node_ids=[],
            required_tools=[],
            risk_tags=[],
            depends_on=[],
            assigned_agent_name="BackendAgent",
            assigned_agent_role="coder",
            agent_configuration={
                "provider": "example",
                "model": "model-1",
                "framework": "portable",
            },
        )
    )
    db.commit()
    RepositoryAuditService(db).record(
        task.id,
        "patch_applied",
        ".",
        {
            "changed_files": ["checkout.py"],
            "diff_sha256": "diff-hash",
        },
    )

    trace = TraceabilityService(db).refresh(task.id)

    assert trace.coverage.covered_requirements == 1
    assert [
        item.text for item in trace.coverage.uncovered_requirements
    ] == ["A receipt is recorded."]
    assert trace.coverage.link_count_by_target_type["execution_plan_step"] == 1
    assert trace.coverage.link_count_by_target_type["changed_file"] == 1
    assert {
        link.target_type
        for link in trace.links
        if link.requirement_id == trace.requirements[0].id
    } >= {"execution_plan_step", "agent", "context_event", "diff", "changed_file"}


def test_external_link_submission_preserves_explicit_provenance(db):
    task, _, _ = TaskIntakeService(db).ingest(_contract("external-link"))
    requirement = TraceabilityService(db).refresh(task.id).requirements[0]

    link = TraceabilityService(db).submit_link(
        task.id,
        RequirementLinkCreateV1(
            requirement_id=requirement.id,
            target_type="commit",
            target_id="abc123",
            relation="implemented_by",
            metadata={"integration": "ci"},
        ),
        actor="ci-bot",
    )
    refreshed = TraceabilityService(db).refresh(task.id)

    assert link.source == "external"
    assert link.created_by == "ci-bot"
    assert any(item.id == link.id for item in refreshed.links)


def test_evidence_pack_v2_is_v1_compatible_and_redacts_secrets(
    db, tmp_path, monkeypatch
):
    run = RunService(db).create(
        RunCreate(
            title="Evidence compatibility",
            description="Payment succeeds for a valid cart.",
        )
    )
    run.task.contract_version = "task-contract/v1"
    run.task.connector_type = "generic"
    run.task.external_id = "evidence-v2"
    run.task.task_contract = {
        **_contract("evidence-v2").model_dump(mode="json"),
        "metadata": {
            "api_key": "must-not-leak",
            "nested": {"password": "also-secret"},
        },
    }
    run.task.readiness_score = 1.0
    run.task.readiness_details = {"ready": True}
    db.commit()
    monkeypatch.setenv("SACM_EVIDENCE_HMAC_KEY", "signature-key")

    pack = EvidenceService(db, root=str(tmp_path)).build(run.id)
    manifest = json.loads(
        (tmp_path / run.id / "run-manifest.json").read_text()
    )
    serialized = json.dumps(manifest)

    assert manifest["schema_version"] == "run-manifest/v2"
    assert manifest["run_id"] == run.id
    assert manifest["task_id"] == run.task_id
    assert manifest["evidence_pack_version"] == "2.0"
    assert manifest["traceability"]["schema_version"] == "traceability/v1"
    assert manifest["integrity"]["signature"]["present"] is True
    assert "must-not-leak" not in serialized
    assert "also-secret" not in serialized
    assert pack.manifest_hash


def test_traceability_api_requires_authentication(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    task, _, _ = TaskIntakeService(db).ingest(_contract("trace-api"))

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            path = f"/v1/tasks/{task.id}/requirements"
            assert client.get(path).status_code == 401
            response = client.get(
                path, headers={"X-SACM-Actor": "developer"}
            )
            assert response.status_code == 200
            requirement_id = response.json()[0]["id"]
            link = client.post(
                f"/v1/tasks/{task.id}/traceability/links",
                headers={"X-SACM-Actor": "developer"},
                json={
                    "requirement_id": requirement_id,
                    "target_type": "commit",
                    "target_id": "deadbeef",
                    "relation": "implemented_by",
                },
            )
            assert link.status_code == 201
            trace = client.get(
                f"/v1/tasks/{task.id}/traceability",
                headers={"X-SACM-Actor": "developer"},
            )
            assert trace.status_code == 200
            assert trace.json()["coverage"]["covered_requirements"] == 1
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
