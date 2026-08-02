import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.event_service import EventService
from sacm.core.evidence_service import EvidenceService
from sacm.core.run_service import RunService
from sacm.core.verifier import Verifier
from sacm.infrastructure.db.models import Artifact, Base
from sacm.infrastructure.db.session import get_db
from sacm.schemas.result import AgentResult
from sacm.schemas.run import RunCreate


def _task(db):
    run = RunService(db).create(
        RunCreate(title="Retry payments", description="Fix payment retries.")
    )
    run.task.task_contract = {
        "schema_version": "task-contract/v1",
        "connector_type": "generic",
        "external_id": "PAY-142",
        "title": "Retry payments",
        "description": "Fix payment retries.",
        "acceptance_criteria": [
            "Stop retrying after the third transient failure.",
            "Preserve the idempotency key across retries.",
        ],
        "repositories": [],
    }
    db.add_all(
        [
            Artifact(
                task_id=run.task_id,
                artifact_type="verification",
                content_hash=character * 64,
                metadata_={"run_id": run.id},
            )
            for character in ("a", "b", "c", "d", "e", "f", "1")
        ]
    )
    db.commit()
    return run.task


def _actions():
    requirement_actions = [
        {
            "type": "REQUIREMENT_VERIFICATION",
            "requirement_text": text,
            "passed": True,
            "implementation_references": ["src/RetryService.java:81-112"],
            "test_references": [test],
            "verification_commands": ["./mvnw test"],
            "evidence_integrity": "VALID",
            "evidence": [{"sha256": "a" * 64}],
        }
        for text, test in (
            (
                "Stop retrying after the third transient failure.",
                "PaymentRetryTest.shouldStopAfterThirdAttempt",
            ),
            (
                "Preserve the idempotency key across retries.",
                "PaymentRetryTest.shouldPreserveIdempotencyKey",
            ),
        )
    ]
    return [
        *requirement_actions,
        {
            "type": "BUILD_RESULT",
            "passed": True,
            "deterministic": True,
            "exit_code": 0,
            "command": "./mvnw package",
            "evidence": [{"sha256": "d" * 64}],
        },
        {
            "type": "TEST_RESULT",
            "scope": "focused",
            "passed": True,
            "deterministic": True,
            "exit_code": 0,
            "failed_before_fix": True,
            "base_revision_exit_code": 1,
            "base_revision_command": "./mvnw -Dtest=PaymentRetryTest test@base",
            "command": "./mvnw -Dtest=PaymentRetryTest test",
            "tests": [
                "PaymentRetryTest.shouldStopAfterThirdAttempt",
                "PaymentRetryTest.shouldPreserveIdempotencyKey",
            ],
            "evidence": [{"sha256": "b" * 64}],
        },
        {
            "type": "TEST_RESULT",
            "scope": "affected-regression",
            "passed": True,
            "deterministic": True,
            "exit_code": 0,
            "command": "./mvnw test",
            "evidence": [{"sha256": "c" * 64}],
        },
        {
            "type": "CONTRACT_COMPATIBILITY",
            "status": "PASS",
            "passed": True,
            "deterministic": True,
            "exit_code": 0,
            "command": "openapi-diff",
            "checks": ["openapi-diff"],
            "evidence": [{"sha256": "e" * 64}],
        },
        {
            "type": "SECURITY_RESULT",
            "status": "PASS",
            "passed": True,
            "deterministic": True,
            "exit_code": 0,
            "command": "semgrep",
            "evidence": [{"sha256": "f" * 64}],
        },
        {
            "type": "TEST_INTEGRITY",
            "status": "PASS",
            "passed": True,
            "deterministic": True,
            "exit_code": 0,
            "command": "test-integrity",
            "tests_removed": [],
            "weakened_assertions": [],
            "evidence": [{"sha256": "1" * 64}],
        },
    ]


def _result(actions, *, confidence=0.0, next_state_hint="blocked"):
    tool_executions = [
        {
            "type": "tool_execution",
            "command": command,
            "returncode": returncode,
        }
        for command, returncode in (
            ("./mvnw package", 0),
            ("./mvnw -Dtest=PaymentRetryTest test", 0),
            ("./mvnw -Dtest=PaymentRetryTest test@base", 1),
            ("./mvnw test", 0),
            ("openapi-diff", 0),
            ("semgrep", 0),
            ("test-integrity", 0),
        )
    ]
    return AgentResult(
        agent_name="Verifier",
        summary="Verification matrix evaluated.",
        actions=actions,
        artifacts=tool_executions,
        confidence=confidence,
        next_state_hint=next_state_hint,
    )


def test_legacy_review_reuses_prior_successful_verification(db):
    run = RunService(db).create(
        RunCreate(title="Review verified change", description="Review the diff.")
    )
    EventService(db).save(
        run.task_id,
        "agent_result",
        {
            "agent_task_contract": {"run_id": run.id},
            "actions": [{"type": "VERIFICATION", "passed": True}],
        },
    )
    review = AgentResult(
        agent_name="Reviewer",
        summary="Review complete.",
        actions=[{"type": "REVIEW", "description": "Inspected the diff."}],
        artifacts=[],
        confidence=0.8,
        next_state_hint="done",
    )

    matrix = Verifier(db).evaluate(run.task, review, run_id=run.id)

    assert Verifier.has_successful_verification(review) is False
    assert matrix.complete is True


def test_verifier_v2_requires_every_acceptance_criterion(db):
    task = _task(db)
    actions = _actions()
    actions.pop(1)

    matrix = Verifier(db).evaluate(
        task,
        _result(actions),
        run_id=task.runs[0].id,
    )

    assert matrix.strict is True
    assert matrix.complete is False
    assert [item.status for item in matrix.requirements] == ["PASS", "MISSING"]
    assert "Not all mandatory acceptance criteria" in matrix.blocking_reasons[0]


def test_verifier_v2_rejects_removed_or_weakened_tests(db):
    task = _task(db)
    actions = _actions()
    integrity = next(item for item in actions if item["type"] == "TEST_INTEGRITY")
    integrity["tests_removed"] = ["PaymentRetryTest.testLegacyRetry"]
    integrity["weakened_assertions"] = ["assert retries <= 3 -> assert retries <= 5"]

    matrix = Verifier(db).evaluate(
        task,
        _result(actions),
        run_id=task.runs[0].id,
    )

    assert matrix.complete is False
    assert matrix.test_integrity.status == "FAIL"


def test_verifier_v2_requires_pre_fix_regression_failure(db):
    task = _task(db)
    actions = _actions()
    focused = next(
        item
        for item in actions
        if item["type"] == "TEST_RESULT" and item["scope"] == "focused"
    )
    focused["failed_before_fix"] = False

    matrix = Verifier(db).evaluate(
        task,
        _result(actions),
        run_id=task.runs[0].id,
    )

    assert matrix.complete is False
    assert matrix.regression.status == "FAIL"


def test_verifier_v2_is_independent_of_agent_confidence(db):
    task = _task(db)

    verifier = Verifier(db)
    matrix = verifier.evaluate(
        task,
        _result(_actions(), confidence=0.0, next_state_hint="blocked"),
        run_id=task.runs[0].id,
    )
    matrix = verifier.finalize_evidence(matrix, evidence_valid=True)

    assert matrix.complete is True
    assert matrix.build_status == "PASS"
    assert matrix.regression.status == "PASS"
    assert matrix.contract_compatibility.status == "PASS"
    assert matrix.security_status == "PASS"
    assert matrix.test_integrity.status == "PASS"
    assert matrix.evidence_complete is True


def test_verifier_v2_rejects_action_without_recorded_tool_execution(db):
    task = _task(db)
    result = _result(_actions(), confidence=1.0)
    result.artifacts = [
        item
        for item in result.artifacts
        if item.get("command") != "./mvnw package"
    ]

    matrix = Verifier(db).evaluate(
        task,
        result,
        run_id=task.runs[0].id,
    )

    assert matrix.complete is False
    assert matrix.build_status == "FAIL"


def test_verifier_v2_rejects_unbacked_regression_evidence(db):
    task = _task(db)
    actions = _actions()
    focused = next(
        item
        for item in actions
        if item["type"] == "TEST_RESULT" and item["scope"] == "focused"
    )
    focused["evidence"] = [{"sha256": "9" * 64}]

    matrix = Verifier(db).evaluate(
        task,
        _result(actions),
        run_id=task.runs[0].id,
    )

    assert matrix.technical_complete is False
    assert "Regression evidence is missing or invalid." in matrix.blocking_reasons


def test_verifier_v2_honors_explicit_requirement_failure(db):
    task = _task(db)
    actions = _actions()
    actions[0]["passed"] = False

    matrix = Verifier(db).evaluate(
        task,
        _result(actions),
        run_id=task.runs[0].id,
    )

    assert matrix.requirements[0].status == "MISSING"
    assert matrix.technical_complete is False


def test_verification_matrix_api_and_evidence_pack_are_durable(db, tmp_path):
    task = _task(db)
    run = task.runs[0]
    verifier = Verifier(db)
    matrix = verifier.evaluate(task, _result(_actions()), run_id=run.id)
    provisional = EvidenceService(db, root=str(tmp_path / "evidence")).build(
        run.id,
        trusted_internal=True,
    )
    provisional_result = EvidenceService(
        db, root=str(tmp_path / "evidence")
    ).verify(run.id, provisional.id, trusted_internal=True)
    matrix = verifier.finalize_evidence(
        matrix,
        evidence_valid=provisional_result.status != "INVALID",
    )
    EventService(db).save(
        task.id,
        "verification_matrix_v2",
        matrix.model_dump(mode="json"),
    )
    pack = EvidenceService(db, root=str(tmp_path / "evidence")).build(
        run.id,
        trusted_internal=True,
    )
    manifest = json.loads(
        (Path(pack.path) / "run-manifest.json").read_text()
    )

    assert manifest["verification_matrix"]["complete"] is True
    assert (
        EvidenceService(db, root=str(tmp_path / "evidence"))
        .verify(run.id, pack.id, trusted_internal=True)
        .status
        != "INVALID"
    )



def test_verification_matrix_api_is_authenticated():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db = sessionmaker(bind=engine)()
    Base.metadata.create_all(engine)
    task = _task(db)
    run = task.runs[0]
    verifier = Verifier(db)
    matrix = verifier.finalize_evidence(
        verifier.evaluate(task, _result(_actions()), run_id=run.id),
        evidence_valid=True,
    )
    EventService(db).save(
        task.id,
        "verification_matrix_v2",
        matrix.model_dump(mode="json"),
    )

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            assert client.get(f"/v1/runs/{run.id}/verification").status_code == 401
            response = client.get(
                f"/v1/runs/{run.id}/verification",
                headers={"X-SACM-Actor": "developer"},
            )
            assert response.status_code == 200
            assert response.json()["complete"] is True
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
