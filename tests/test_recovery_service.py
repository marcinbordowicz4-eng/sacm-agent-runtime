import pytest
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


def test_failure_classifier_treats_jest_sigkill_as_environment_failure(db):
    report = RecoveryService(db).classify(
        {
            "type": "VerificationFailure",
            "message": "Jest exited with code 137 after a worker received SIGKILL.",
            "details": {"tool": "jest", "exit_code": 137},
        }
    )

    assert report.classification == FailureClassification.ENVIRONMENT


@pytest.mark.parametrize(
    ("tool", "output", "expected"),
    [
        ("javac", "src/App.java:12: error: cannot find symbol", "COMPILATION"),
        ("mypy", "app.py:7: error: Incompatible types [assignment]", "COMPILATION"),
        ("ruff", "app.py:3:1: F821 Undefined name `value`", "COMPILATION"),
        ("tsc", "src/app.ts(4,2): error TS2322: Type mismatch", "COMPILATION"),
        ("pytest", "FAILED tests/test_api.py::test_create - assert 1 == 2", "TEST_REGRESSION"),
        ("jest", " FAIL src/app.test.ts", "TEST_REGRESSION"),
        (
            "go test",
            '{"Action":"fail","Test":"TestCreate","Output":"want 2 got 1"}',
            "TEST_REGRESSION",
        ),
        (
            "junit",
            '<testsuite><testcase name="creates"><failure message="bad"/></testcase></testsuite>',
            "TEST_REGRESSION",
        ),
        ("gradle", "Execution failed: permission denied", "ENVIRONMENT"),
        ("terraform", "Error: connection refused", "ENVIRONMENT"),
        ("helm", "no space left on device", "ENVIRONMENT"),
        ("kubeconform", "network is unreachable", "ENVIRONMENT"),
        ("runtime", "contract mismatch for PaymentApi", "API_INCOMPATIBILITY"),
        ("runtime", "unexpected keyword argument retry", "API_INCOMPATIBILITY"),
        ("runtime", "Cannot find module payments", "MISSING_CONTEXT"),
        ("runtime", "Unresolved reference FraudClient", "MISSING_CONTEXT"),
        ("runtime", "boundary violation: domain imports api", "ARCHITECTURE_MISMATCH"),
        ("runtime", "invalid plan omitted database migration", "BAD_PLAN"),
        ("runtime", "wrong assumption about retry semantics", "WRONG_ASSUMPTION"),
        ("runtime", "repeated patch made no progress", "MODEL_STUCK"),
    ],
)
def test_structured_diagnostic_engine_classifies_synthetic_failures(
    db, tool, output, expected
):
    report = RecoveryService(db).classify(
        {
            "type": "CommandFailure",
            "message": f"{tool} command failed",
            "diagnostic_bundle": {
                "tool": tool,
                "command": f"{tool} test",
                "exit_code": 1,
                "raw_output": output,
                "changed_symbols": ["PaymentService.retry"],
                "affected_requirements": ["AC-3"],
            },
        }
    )

    assert report.classification.value == expected
    assert report.reason_codes
    assert report.diagnosis_fingerprint
    assert report.stages[-1] == "RECOVERY_POLICY"
    assert report.confidence >= 0.6


def test_low_confidence_diagnosis_escalates_to_human(db):
    run, step = _failed_step(db)

    _, report, decision = RecoveryService(db).handle(
        run.id,
        step.id,
        {"type": "UnknownFailure", "message": "opaque failure 42"},
    )

    assert report.confidence < 0.6
    assert decision.action == RecoveryAction.ESCALATE
    assert "below the autonomous threshold" in decision.reason


def test_identical_patch_and_root_cause_are_not_retried(db):
    run, step = _failed_step(db)
    failure = {
        "type": "SyntaxError",
        "message": "compile failed in checkout.py",
        "patch_hash": "patch-123",
    }
    RecoveryService(db).handle(run.id, step.id, failure)
    RunService(db).start_step(run.id, step.id)
    RunService(db).fail_step(run.id, step.id, failure)

    _, _, decision = RecoveryService(db).handle(run.id, step.id, failure)

    assert decision.action == RecoveryAction.ESCALATE
    assert "same patch and root cause" in decision.reason


def test_legacy_evidence_is_preserved_and_deduplicated(db):
    service = RecoveryService(db)
    evidence = {
        "kind": "compiler",
        "source": "javac",
        "message": "cannot find symbol",
        "file": "src/App.java",
        "line": 12,
    }

    report = service.classify(
        {
            "type": "CompilerError",
            "message": "Compilation failed.",
            "evidence": [evidence],
            "diagnostic_bundle": {
                "tool": "javac",
                "compiler_diagnostics": [evidence],
            },
        }
    )
    repeated = service.classify(report.model_dump(mode="json"))

    assert report.evidence == [evidence | {"code": None, "test_name": None, "requirement_id": None}]
    assert repeated == report


def test_different_root_causes_have_different_fingerprints(db):
    service = RecoveryService(db)

    first = service.classify(
        {
            "type": "SyntaxError",
            "message": "compile failed in a.py",
            "patch_hash": "patch-123",
        }
    )
    second = service.classify(
        {
            "type": "SyntaxError",
            "message": "compile failed in b.py",
            "patch_hash": "patch-123",
        }
    )

    assert first.root_cause == "compile failed in a.py"
    assert second.root_cause == "compile failed in b.py"
    assert first.diagnosis_fingerprint != second.diagnosis_fingerprint


def test_explicit_human_action_overrides_low_confidence_gate(db):
    run, step = _failed_step(db)
    report = RecoveryService(db).classify(
        {"type": "UnknownFailure", "message": "opaque failure 42"}
    )

    decision = RecoveryService(db).decide(
        RunService(db).get(run.id),
        report,
        requested_action=RecoveryAction.REPLAN,
    )

    assert report.confidence < 0.6
    assert decision.action == RecoveryAction.REPLAN
    assert decision.status == "SCHEDULED"


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
