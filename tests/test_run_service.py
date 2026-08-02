import hashlib
import json

import pytest

from sacm.core.event_service import EventService
from sacm.core.evidence_service import EvidenceService
from sacm.core.external_agent_service import ExternalAgentService
from sacm.core.local_workflow import LocalWorkflow
from sacm.core.run_service import RunService
from sacm.core.workflow_backend import LocalWorkflowBackend, workflow_backend
from sacm.core.workspace import WorkspaceManager, WorkspaceRef
from sacm.infrastructure.db.models import AgentOutcomeAnalytics
from sacm.schemas.context import AgentContext
from sacm.schemas.contracts import (
    AgentResultV1,
    AgentTaskV1,
    ExternalAgentStepCreate,
)
from sacm.schemas.result import AgentResult
from sacm.schemas.run import RunCreate


def _create_run(db):
    return RunService(db).create(
        RunCreate(title="Fix tests", description="Fix the failing checkout test")
    )


def test_run_events_are_ordered_and_hash_chained(db):
    service = RunService(db)
    run = _create_run(db)
    service.transition(run.id, "PLANNING", "RunStarted")
    service.transition(run.id, "IMPLEMENTING", "WorkflowImplementing")

    events = service.events(run.id)

    assert [event.sequence for event in events] == [1, 2, 3]
    assert events[1].previous_event_hash == events[0].event_hash
    payload = {
        "run_id": run.id,
        "step_id": None,
        "sequence": events[2].sequence,
        "event_type": events[2].event_type,
        "actor": events[2].actor,
        "payload": events[2].payload,
        "previous_event_hash": events[2].previous_event_hash,
        "occurred_at": events[2].occurred_at.isoformat(),
    }
    assert (
        events[2].event_hash
        == hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_run_rejects_invalid_state_transition(db):
    run = _create_run(db)

    with pytest.raises(ValueError, match="Invalid transition"):
        RunService(db).transition(run.id, "COMPLETED", "RunCompleted")


def test_run_requires_evidence_before_completion(db, tmp_path):
    service = RunService(db)
    run = _create_run(db)
    service.transition(run.id, "PLANNING", "RunStarted")
    service.transition(run.id, "IMPLEMENTING", "WorkflowImplementing")
    service.transition(run.id, "REVIEWING", "WorkflowReviewing")
    service.transition(run.id, "TESTING", "WorkflowTesting")
    service.transition(run.id, "DELIVERING", "WorkflowDelivering")

    with pytest.raises(ValueError, match="evidence pack"):
        service.transition(run.id, "COMPLETED", "RunCompleted")

    EvidenceService(db, root=str(tmp_path)).build(run.id)
    assert service.transition(run.id, "COMPLETED", "RunCompleted").status == "COMPLETED"


def test_failed_step_can_be_retried_without_duplication(db):
    service = RunService(db)
    run = _create_run(db)
    step = service.add_step(run.id, "test", {}, f"{run.id}:test")
    service.start_step(run.id, step.id)
    service.fail_step(run.id, step.id, {"type": "TestFailure"})

    retried = service.retry_step(run.id, step.id)

    assert retried.status == "PENDING"
    assert retried.retry_count == 1
    assert service.add_step(run.id, "test", {}, f"{run.id}:test").id == step.id


def test_failed_run_can_resume_and_cancelled_run_cannot(db):
    service = RunService(db)
    run = _create_run(db)
    service.transition(run.id, "PLANNING", "RunStarted")
    service.transition(run.id, "FAILED", "RunFailed")

    resumed = service.resume(run.id)
    assert resumed.status == "PLANNING"
    assert resumed.completed_at is None
    assert service.cancel(run.id).status == "CANCELLED"
    with pytest.raises(ValueError, match="cannot be cancelled"):
        service.cancel(run.id)


def test_recovery_marks_interrupted_step_failed_after_process_restart(db):
    service = RunService(db)
    run = _create_run(db)
    service.transition(run.id, "PLANNING", "RunStarted")
    service.transition(run.id, "IMPLEMENTING", "WorkflowImplementing")
    step = service.add_step(run.id, "implementation", {}, f"{run.id}:implementation")
    service.start_step(run.id, step.id)

    recovered = RunService(db).recover_interrupted(run.id)

    assert recovered.status == "FAILED"
    assert RunService(db).list_steps(run.id)[0].status == "FAILED"
    assert RunService(db).events(run.id)[-1].event_type == "RunRecoveryDetected"


def test_local_workflow_backend_is_default(db, monkeypatch):
    monkeypatch.delenv("SACM_WORKFLOW_BACKEND", raising=False)

    assert isinstance(workflow_backend(db), LocalWorkflowBackend)


def test_evidence_pack_contains_manifest_events_and_checksums(db, tmp_path):
    service = RunService(db)
    run = _create_run(db)
    service.transition(run.id, "PLANNING", "RunStarted")

    pack = EvidenceService(db, root=str(tmp_path)).build(run.id)
    directory = tmp_path / run.id

    assert (directory / "run-manifest.json").exists()
    assert (directory / "events.jsonl").exists()
    assert (directory / "checksums.sha256").exists()
    assert (
        pack.manifest_hash
        == hashlib.sha256((directory / "run-manifest.json").read_bytes()).hexdigest()
    )


def test_evidence_pack_records_artifacts_and_provenance(db, tmp_path, monkeypatch):
    run = _create_run(db)
    task = AgentTaskV1(
        run_id=run.id,
        step_id="step-1",
        role="reviewer",
        objective="Review the patch.",
        token_budget=100,
        timeout_seconds=60,
    )
    result = AgentResultV1(
        run_id=run.id,
        step_id="step-1",
        status="COMPLETED",
        summary="Reviewed the change.",
        artifacts=[
            {
                "artifact_type": "diff",
                "metadata": {"content": "diff --git a/a.py b/a.py\n"},
            },
            {
                "artifact_type": "verification",
                "metadata": {"passed": True, "command": "pytest"},
            },
        ],
        confidence=0.9,
        next_state_hint="testing",
    )
    EventService(db).save_agent_result(
        run.task_id,
        "Reviewer",
        AgentResult(
            agent_name="Reviewer",
            summary=result.summary,
            confidence=result.confidence,
            next_state_hint=result.next_state_hint,
        ),
        task_contract=task,
        result_contract=result,
    )
    monkeypatch.setenv("SACM_EVIDENCE_HMAC_KEY", "test-key")

    EvidenceService(db, root=str(tmp_path)).build(run.id)
    directory = tmp_path / run.id

    assert (directory / "patch.diff").read_text() == "diff --git a/a.py b/a.py\n"
    assert (directory / "review-report.json").exists()
    assert (directory / "verification-results.json").exists()
    assert not (directory / "sbom.spdx.json").exists()
    assert (directory / "provenance.intoto.jsonl").exists()
    assert (directory / "signature.sig").exists()


def test_evidence_pack_ingests_verified_external_ci_artifact(db, tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    junit = repository / "junit.xml"
    junit.write_text("<testsuites/>")
    run = RunService(db).create(
        RunCreate(
            title="Test artifact",
            description="Collect JUnit.",
            target_repo_path=str(repository),
        )
    )

    EvidenceService(db, root=str(tmp_path / "evidence")).ingest_artifact(
        run.id, "test_results_junit", str(junit)
    )
    EvidenceService(db, root=str(tmp_path / "evidence")).build(run.id)

    assert (tmp_path / "evidence" / run.id / "test-results.xml").read_text() == (
        "<testsuites/>"
    )


def test_local_workflow_persists_a_verified_completed_run(db, monkeypatch):
    class FakeOrchestrator:
        def __init__(self, _db):
            pass

        def run_task(self, task_id, **kwargs):
            return {"task_id": task_id, "status": "done", "steps": 1}

    monkeypatch.setattr("sacm.core.local_workflow.Orchestrator", FakeOrchestrator)
    run = _create_run(db)

    result = LocalWorkflow(db).execute(run.id)

    assert result["status"] == "COMPLETED"
    assert RunService(db).list_steps(run.id)[0].status == "COMPLETED"


def test_local_workflow_repairs_failed_step_autonomously(db, monkeypatch):
    class FailingOrchestrator:
        calls = 0

        def __init__(self, _db):
            pass

        def run_task(self, task_id, **kwargs):
            self.__class__.calls += 1
            if self.__class__.calls == 1:
                raise RuntimeError("temporary failure")
            assert kwargs["recovery_context"]["decision"]["action"] == "RETRY"
            return {"task_id": task_id, "status": "done", "steps": 1}

    monkeypatch.setattr("sacm.core.local_workflow.Orchestrator", FailingOrchestrator)
    run = _create_run(db)
    workflow = LocalWorkflow(db)

    assert workflow.execute(run.id)["status"] == "COMPLETED"
    assert len(RunService(db).list_steps(run.id)) == 1
    stored = RunService(db).get(run.id)
    assert stored.recovery_attempt_count == 1
    assert stored.last_recovery_action == "RETRY"


def test_local_workflow_does_not_reopen_completed_run_on_analytics_failure(
    db, monkeypatch
):
    class FakeOrchestrator:
        def __init__(self, _db):
            pass

        def run_task(self, task_id, **kwargs):
            return {"task_id": task_id, "status": "done", "steps": 1}

    monkeypatch.setattr("sacm.core.local_workflow.Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(
        "sacm.core.analytics_service.AnalyticsService.recompute_run",
        lambda *_: (_ for _ in ()).throw(RuntimeError("analytics unavailable")),
    )
    run = _create_run(db)

    result = LocalWorkflow(db).execute(run.id)

    assert result["status"] == "COMPLETED"
    assert result["analytics"]["status"] == "FAILED"
    assert RunService(db).get(run.id).status == "COMPLETED"
    assert RunService(db).list_steps(run.id)[0].status == "COMPLETED"


def test_late_executor_failure_does_not_overwrite_newer_resumed_phase(
    db, monkeypatch
):
    run = _create_run(db)

    class StaleFailingOrchestrator:
        def __init__(self, _db):
            self.db = _db

        def run_task(self, task_id, **kwargs):
            service = RunService(self.db)
            service.transition(run.id, "FAILED", "ManualFailure")
            service.resume(run.id)
            raise RuntimeError("late executor failure")

    monkeypatch.setattr(
        "sacm.core.local_workflow.Orchestrator", StaleFailingOrchestrator
    )

    result = LocalWorkflow(db).execute(run.id)

    assert result["status"] == "PLANNING"
    assert result["stale_result_ignored"] is True
    assert RunService(db).get(run.id).status == "PLANNING"


def test_external_framework_failure_schedules_typed_recovery(db):
    run = _create_run(db)
    service = ExternalAgentService(db)
    scheduled = service.schedule(
        run.id,
        ExternalAgentStepCreate(
            framework="codex",
            agent_name="coder",
            idempotency_key="codex:coder:failure",
            role="coder",
            objective="Fix checkout.",
            token_budget=500,
            timeout_seconds=120,
        ),
    )

    result = AgentResultV1(
        run_id=run.id,
        step_id=scheduled.step.id,
        status="FAILED",
        summary="Tests failed.",
        failure={
            "classification": "TEST_REGRESSION",
            "type": "TestFailure",
            "message": "pytest checkout failed",
        },
    )
    submission = service.submit(
        run.id,
        scheduled.step.id,
        result,
    )
    repeated = service.submit(run.id, scheduled.step.id, result)

    assert submission.step.status == "PENDING"
    assert submission.recovery.action == "DEBUG"
    assert repeated.recovery.attempt == submission.recovery.attempt
    assert RunService(db).get(run.id).last_failure_classification == "TEST_REGRESSION"
    assert RunService(db).get(run.id).recovery_attempt_count == 1


def test_versioned_contracts_validate_required_fields():
    task = AgentTaskV1(
        run_id="run-1",
        step_id="step-1",
        role="coder",
        objective="Fix the test",
        token_budget=100,
        timeout_seconds=60,
    )
    result = AgentResultV1(
        run_id="run-1",
        step_id="step-1",
        status="COMPLETED",
        summary="Fixed",
    )

    assert task.schema_version == "agent-task/v1"
    assert result.schema_version == "agent-result/v1"


def test_external_framework_step_persists_contract_usage_and_evidence(db):
    run = _create_run(db)
    service = ExternalAgentService(db)
    scheduled = service.schedule(
        run.id,
        ExternalAgentStepCreate(
            framework="langgraph",
            agent_name="checkout-coder",
            idempotency_key="langgraph:checkout-coder:1",
            role="coder",
            objective="Fix the checkout test.",
            token_budget=1_000,
            timeout_seconds=300,
        ),
    )

    submission = service.submit(
        run.id,
        scheduled.step.id,
        AgentResultV1(
            run_id=run.id,
            step_id=scheduled.step.id,
            status="COMPLETED",
            summary="Fixed the checkout test.",
            evidence=[
                {
                    "artifact_type": "verification",
                    "metadata": {"command": "pytest", "passed": True},
                }
            ],
            usage=[
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "input_tokens": 12,
                    "output_tokens": 8,
                    "estimated_cost_usd": 0.01,
                }
            ],
        ),
    )

    assert submission.step.status == "COMPLETED"
    event = EventService(db).get_recent_events(run.task_id)[0]
    assert event.payload["agent_name"] == "langgraph:checkout-coder"
    assert event.payload["usage"][0]["input_tokens"] == 12
    assert event.payload["agent_task_contract"]["run_id"] == run.id
    assert event.payload["agent_result_contract"]["evidence"][0]["metadata"]["passed"]
    assert (
        service.submit(
            run.id,
            scheduled.step.id,
            AgentResultV1.model_validate(submission.step.output),
        ).step.id
        == scheduled.step.id
    )
    assert len(EventService(db).get_recent_events(run.task_id)) == 1
    assert (
        db.query(AgentOutcomeAnalytics)
        .filter(AgentOutcomeAnalytics.run_id == run.id)
        .count()
        == 1
    )


def test_external_framework_step_is_idempotent_and_rejects_payload_change(db):
    run = _create_run(db)
    service = ExternalAgentService(db)
    payload = ExternalAgentStepCreate(
        framework="openhands",
        agent_name="coder",
        idempotency_key="openhands:coder:1",
        role="coder",
        objective="Implement the change.",
        token_budget=500,
        timeout_seconds=120,
    )

    first = service.schedule(run.id, payload)
    second = service.schedule(run.id, payload)

    assert second.step.id == first.step.id
    with pytest.raises(ValueError, match="idempotency key"):
        service.schedule(
            run.id,
            payload.model_copy(update={"objective": "A different change."}),
        )


def test_external_framework_result_requires_matching_contract_identity(db):
    run = _create_run(db)
    scheduled = ExternalAgentService(db).schedule(
        run.id,
        ExternalAgentStepCreate(
            framework="microsoft-agent-framework",
            agent_name="reviewer",
            idempotency_key="maf:reviewer:1",
            role="reviewer",
            objective="Review the patch.",
            token_budget=500,
            timeout_seconds=120,
        ),
    )

    with pytest.raises(ValueError, match="must match"):
        ExternalAgentService(db).submit(
            run.id,
            scheduled.step.id,
            AgentResultV1(
                run_id="wrong-run",
                step_id=scheduled.step.id,
                status="COMPLETED",
                summary="Reviewed.",
            ),
        )


def test_external_framework_result_creates_and_honors_approval(db):
    run = _create_run(db)
    service = ExternalAgentService(db)
    scheduled = service.schedule(
        run.id,
        ExternalAgentStepCreate(
            framework="codex",
            agent_name="delivery",
            idempotency_key="codex:delivery:1",
            role="coder",
            objective="Prepare delivery.",
            token_budget=500,
            timeout_seconds=120,
        ),
    )
    result = AgentResultV1(
        run_id=run.id,
        step_id=scheduled.step.id,
        status="NEEDS_APPROVAL",
        summary="Ready to push the branch.",
        actions=[{"action": "git.push"}],
    )

    waiting = service.submit(run.id, scheduled.step.id, result)

    assert waiting.step.status == "AWAITING_APPROVAL"
    assert waiting.approval_id
    with pytest.raises(ValueError, match="still pending"):
        service.submit(run.id, scheduled.step.id, result)

    from sacm.core.policy_service import PolicyService

    PolicyService(db).decide(
        waiting.approval_id, True, "maintainer", "Approved for delivery."
    )
    completed = service.submit(
        run.id,
        scheduled.step.id,
        result.model_copy(update={"status": "COMPLETED"}),
    )

    assert completed.step.status == "COMPLETED"
    assert completed.approval_id == waiting.approval_id
    assert completed.step.output["sacm_approval_id"] == waiting.approval_id


def test_agent_runs_through_versioned_contract_and_preserves_usage():
    from sacm.agents.base import Agent

    class LegacyAgent(Agent):
        name = "Legacy"
        role = "coding"

        def run(self, context: AgentContext) -> AgentResult:
            assert context.task_id == "task-1"
            return AgentResult(
                agent_name=self.name,
                summary="Implemented the requested change.",
                artifacts=[
                    {
                        "type": "usage",
                        "provider": "openai",
                        "model": "gpt-test",
                        "input_tokens": 12,
                        "output_tokens": 8,
                    }
                ],
                confidence=0.9,
                next_state_hint="testing",
            )

    task = AgentTaskV1(
        run_id="run-1",
        step_id="step-1",
        role="coder",
        objective="Implement the requested change.",
        token_budget=100,
        timeout_seconds=60,
        execution_context={"task_id": "task-1"},
    )

    result = LegacyAgent().run_v1(task)

    assert result.run_id == task.run_id
    assert result.status == "COMPLETED"
    assert result.usage[0].input_tokens == 12
    assert LegacyAgent().result_from_v1(result).artifacts[0]["type"] == "usage"


def test_agent_events_persist_versioned_task_and_result_contracts(db):
    run = _create_run(db)
    task = AgentTaskV1(
        run_id=run.id,
        step_id="step-1",
        role="reviewer",
        objective="Review the change.",
        token_budget=100,
        timeout_seconds=60,
    )
    contract_result = AgentResultV1(
        run_id=run.id,
        step_id="step-1",
        status="COMPLETED",
        summary="Review completed.",
        confidence=0.8,
        next_state_hint="testing",
    )
    legacy_result = AgentResult(
        agent_name="Reviewer",
        summary=contract_result.summary,
        confidence=contract_result.confidence,
        next_state_hint=contract_result.next_state_hint,
    )

    EventService(db).save_agent_result(
        run.task_id,
        "Reviewer",
        legacy_result,
        task_contract=task,
        result_contract=contract_result,
    )

    payload = EventService(db).get_recent_events(run.task_id)[0].payload
    assert payload["agent_task_contract"]["schema_version"] == "agent-task/v1"
    assert payload["agent_result_contract"]["schema_version"] == "agent-result/v1"


def test_workspace_uses_restricted_docker_configuration(monkeypatch):
    command = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, **kwargs):
        command["args"] = args
        return Completed()

    monkeypatch.setattr("sacm.core.workspace.subprocess.run", fake_run)
    workspace = WorkspaceRef(
        run_id="run-1",
        repository_path="/repo",
        path="/workspace",
        branch_name="sacm/run-1/workspace",
    )

    result = WorkspaceManager().execute(workspace, "python:3.12", ["pytest"])

    assert result["returncode"] == 0
    assert "--network" in command["args"]
    assert command["args"][command["args"].index("--network") + 1] == "none"
    assert "--read-only" in command["args"]
    assert "--cap-drop" in command["args"]
    assert "--pids-limit" in command["args"]


def test_workspace_can_use_gvisor_runtime(monkeypatch):
    command = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, **kwargs):
        command["args"] = args
        return Completed()

    monkeypatch.setattr("sacm.core.workspace.subprocess.run", fake_run)
    monkeypatch.setenv("SACM_DOCKER_RUNTIME", "runsc")
    workspace = WorkspaceRef(
        run_id="run-1",
        repository_path="/repo",
        path="/workspace",
        branch_name="sacm/run-1/workspace",
    )

    WorkspaceManager().execute(workspace, "python:3.12", ["pytest"])

    assert command["args"][command["args"].index("--runtime") + 1] == "runsc"
