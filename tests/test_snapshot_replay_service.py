import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.local_workflow import LocalWorkflow
from sacm.core.run_service import RunService
from sacm.core.snapshot_handoff_service import (
    SnapshotHandoffError,
    SnapshotHandoffService,
)
from sacm.core.snapshot_service import SnapshotService
from sacm.infrastructure.db.models import Base, Run, RunReplay, RunSnapshot
from sacm.infrastructure.db.session import get_db
from sacm.schemas.run import RunCreate


def _run(db):
    return RunService(db).create(
        RunCreate(title="Replay checkout", description="Fix checkout tests.")
    )


def _failed_run_with_checkpoint(db):
    runs = RunService(db)
    run = _run(db)
    step = runs.add_step(run.id, "implementation", {"attempt": 1}, "implementation")
    checkpoint = SnapshotService(db).create(run.id, "before implementation")
    db.commit()
    runs.transition(run.id, "PLANNING", "RunStarted")
    runs.transition(run.id, "IMPLEMENTING", "WorkflowImplementing")
    runs.start_step(run.id, step.id)
    runs.fail_step(run.id, step.id, {"type": "TestFailure"})
    runs.transition(run.id, "FAILED", "RunFailed")
    return run, step, checkpoint


def test_snapshot_creation_is_deterministic_and_parented(db):
    run = _run(db)
    snapshots = SnapshotService(db)

    first = snapshots.create(run.id, "operator checkpoint")
    second = snapshots.create(run.id, "operator checkpoint")
    db.commit()
    RunService(db).transition(run.id, "PLANNING", "RunStarted")
    latest = snapshots.list_snapshots(run.id)[-1]

    assert first.id == second.id
    assert first.checksum == second.checksum
    assert latest.parent_snapshot_id == first.id
    assert latest.event_sequence == 2


def test_snapshot_checksum_corruption_is_rejected(db):
    run = _run(db)
    snapshot = SnapshotService(db).list_snapshots(run.id)[0]
    snapshot.step_state = [{"id": "tampered"}]
    db.commit()

    with pytest.raises(ValueError, match="checksum"):
        SnapshotService(db).validate(run, snapshot)


def test_accepted_handoff_fences_stale_scope_owner(db):
    run = _run(db)
    snapshot = SnapshotService(db).list_snapshots(run.id)[0]
    service = SnapshotHandoffService(db)
    handoff = service.create(
        run.id,
        snapshot.id,
        base_sha="a" * 40,
        head_sha="b" * 40,
        context_snapshot_id="context-1",
        closed_subtasks=["plan"],
        open_subtasks=["PaymentService.create"],
        changed_symbols=["PaymentService.create"],
        quorum_notes=[
            {"role": "reviewer", "status": "approved"},
            {"role": "tester", "status": "approved"},
        ],
        evidence_hashes=["c" * 64],
    )
    accepted = service.accept(handoff.id, evaluator="quorum")
    first = service.claim_scope(
        accepted.id, scope_key="PaymentService.create", owner="agent-a", lease_seconds=1
    )
    first_token = first.fencing_token
    first.expires_at = first.expires_at.replace(year=2000)
    db.commit()
    second = service.claim_scope(
        accepted.id, scope_key="PaymentService.create", owner="agent-b"
    )

    assert second.fencing_token == first_token + 1
    with pytest.raises(SnapshotHandoffError, match="stale"):
        service.validate_fencing(
            first.id, owner="agent-a", fencing_token=first_token
        )


def test_restore_is_distinct_from_failed_run_resume(db):
    run, step, checkpoint = _failed_run_with_checkpoint(db)

    restored, restored_steps = SnapshotService(db).restore(
        run.id, checkpoint.id, "retry from clean boundary"
    )

    assert restored.status == "CREATED"
    assert restored.completed_at is None
    assert restored_steps == [step.id]
    assert RunService(db).get_step(run.id, step.id).status == "PENDING"
    assert RunService(db).events(run.id)[-1].event_type == "SnapshotRestored"
    with pytest.raises(ValueError, match="Only failed runs"):
        RunService(db).resume(run.id)


def test_restore_rejects_stale_snapshot_step_topology(db):
    run = _run(db)
    initial = SnapshotService(db).list_snapshots(run.id)[0]
    RunService(db).add_step(run.id, "implementation", {}, "implementation")
    RunService(db).transition(run.id, "PLANNING", "RunStarted")
    RunService(db).transition(run.id, "FAILED", "RunFailed")

    with pytest.raises(ValueError, match="topology"):
        SnapshotService(db).restore(run.id, initial.id, "stale restore")


def test_replay_creates_immutable_linked_run_with_override_metadata(db):
    source, _, checkpoint = _failed_run_with_checkpoint(db)
    source_event_count = len(RunService(db).events(source.id))
    source_status = source.status

    link = SnapshotService(db).replay(
        source.id,
        checkpoint.id,
        "compare a different model",
        {
            "model": "gpt-replay",
            "provider": "openai",
            "framework": "langgraph",
        },
    )
    replay = db.query(Run).filter(Run.id == link.replay_run_id).one()

    assert replay.id != source.id
    assert replay.task_id == source.task_id
    assert source.status == source_status
    assert len(RunService(db).events(source.id)) == source_event_count
    assert link.source_snapshot_id == checkpoint.id
    assert link.overrides == {
        "model": "gpt-replay",
        "provider": "openai",
        "framework": "langgraph",
    }
    comparison = SnapshotService(db).comparison(replay.id)
    assert comparison["source_run_id"] == source.id
    assert comparison["replay_run_id"] == replay.id
    assert comparison["comparison_status"] == "in_progress"
    assert {"status", "steps", "cost", "usage", "evidence", "failures"} <= set(
        comparison["source"]
    )


def test_replay_is_ready_for_independent_execution(db, monkeypatch, tmp_path):
    class FakeOrchestrator:
        def __init__(self, _db):
            pass

        def run_task(self, task_id, **kwargs):
            return {"task_id": task_id, "status": "done", "steps": 1}

    monkeypatch.setattr("sacm.core.local_workflow.Orchestrator", FakeOrchestrator)
    monkeypatch.setenv("SACM_EVIDENCE_ROOT", str(tmp_path / "evidence"))
    source = _run(db)
    RunService(db).add_step(
        source.id,
        "legacy-orchestrator",
        {"task_id": source.task_id},
        f"{source.id}:legacy-orchestrator",
    )
    checkpoint = SnapshotService(db).create(source.id, "replay boundary")
    db.commit()

    link = SnapshotService(db).replay(
        source.id, checkpoint.id, "execute independently", {}
    )
    result = LocalWorkflow(db).execute(link.replay_run_id)

    assert result["status"] == "COMPLETED"
    assert RunService(db).get(source.id).status == "CREATED"


def test_snapshot_replay_api_requires_authentication_and_exposes_comparison(
    tmp_path, monkeypatch
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = _run(db)
    snapshot = SnapshotService(db).list_snapshots(run.id)[0]

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            path = f"/v1/runs/{run.id}/snapshots"
            assert client.get(path).status_code == 401
            headers = {"X-SACM-Actor": "developer"}
            assert client.get(path, headers=headers).status_code == 200
            created = client.post(
                path,
                headers=headers,
                json={"schema_version": "snapshot-create/v1", "reason": "api"},
            )
            assert created.status_code == 201
            replay = client.post(
                f"/v1/runs/{run.id}/replay",
                headers=headers,
                json={
                    "schema_version": "snapshot-replay/v1",
                    "snapshot_id": snapshot.id,
                    "reason": "API comparison",
                    "overrides": {"model": "gpt-api"},
                },
            )
            assert replay.status_code == 201
            body = replay.json()
            assert body["overrides"]["model"] == "gpt-api"
            comparison = client.get(
                f"/v1/runs/{body['replay_run_id']}/comparison",
                headers=headers,
            )
            assert comparison.status_code == 200
            assert comparison.json()["source_snapshot_id"] == snapshot.id
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_replay_link_is_unique_per_replay_run(db):
    run = _run(db)
    snapshot = SnapshotService(db).list_snapshots(run.id)[0]
    link = SnapshotService(db).replay(run.id, snapshot.id, "baseline", {})

    assert (
        db.query(RunReplay).filter(RunReplay.replay_run_id == link.replay_run_id).count()
        == 1
    )
    assert db.query(RunSnapshot).filter(RunSnapshot.run_id == link.replay_run_id).count()
