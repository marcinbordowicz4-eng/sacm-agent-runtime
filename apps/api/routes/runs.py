from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sacm.core.auth_service import (
    production_mode,
    require_authenticated_actor,
)
from sacm.core.evidence_service import EvidenceService
from sacm.core.external_agent_service import ExternalAgentService
from sacm.core.recovery_service import RecoveryService
from sacm.core.run_context_service import RunContextService
from sacm.core.run_service import RunService
from sacm.core.snapshot_service import SnapshotService
from sacm.core.tenancy_service import (
    AuthorizationError,
    ResourceAuthorizationService,
    TenancyService,
)
from sacm.core.workflow_backend import workflow_backend
from sacm.infrastructure.db.models import EvidencePack
from sacm.infrastructure.db.session import get_db
from sacm.schemas.contracts import (
    ExternalAgentResultSubmit,
    ExternalAgentStepCreate,
)
from sacm.schemas.recovery import RecoveryApplyV1, RecoveryStateV1
from sacm.schemas.run import RunCreate, RunRead, RunStepRead
from sacm.schemas.snapshot import (
    ReplayComparisonV1,
    ReplayCreatedV1,
    RunSnapshotV1,
    SnapshotCreateV1,
    SnapshotReplayV1,
    SnapshotRestoreResultV1,
    SnapshotRestoreV1,
)
from sacm.schemas.supply_chain import VerificationResultV1

router = APIRouter()


class EvidenceArtifactIngest(BaseModel):
    artifact_type: str
    source_path: str


def _run_or_404(service: RunService, run_id: str):
    run = service.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _authorize_run(db: Session, run_id: str, actor: str, permission: str | None = None):
    required_permission = permission or "runs.write"
    try:
        return ResourceAuthorizationService(db).require_run(
            run_id, actor, required_permission
        )
    except AuthorizationError as exc:
        detail = (
            str(exc) if permission is not None else "Insufficient organization role."
        )
        raise HTTPException(status_code=403, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", response_model=RunRead, status_code=201)
def create_run(
    payload: RunCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RunRead:
    if production_mode() and not payload.project_id:
        raise HTTPException(
            status_code=422,
            detail="project_id is required for production runs.",
        )
    if payload.project_id:
        try:
            project = TenancyService(db).require_project_permission(
                payload.project_id,
                actor,
                "runs.write",
                resource_type="run",
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if project.repository_path:
            if (
                payload.target_repo_path
                and payload.target_repo_path != project.repository_path
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Run repository path must match its project repository.",
                )
            payload.target_repo_path = project.repository_path
    return RunRead.model_validate(RunService(db).create(payload))


@router.get("", response_model=list[RunRead])
def list_runs(
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[RunRead]:
    runs = ResourceAuthorizationService(db).accessible_runs(actor)
    return [RunRead.model_validate(run) for run in runs]


@router.get("/{run_id}", response_model=RunRead)
def get_run(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RunRead:
    return RunRead.model_validate(_authorize_run(db, run_id, actor, "runs.read"))


@router.get("/{run_id}/context")
def get_run_context(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    run = _authorize_run(db, run_id, actor, "runs.read")
    return RunContextService(db).build(run)


@router.get("/{run_id}/steps", response_model=list[RunStepRead])
def list_steps(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[RunStepRead]:
    service = RunService(db)
    _authorize_run(db, run_id, actor, "runs.read")
    return [RunStepRead.model_validate(step) for step in service.list_steps(run_id)]


@router.get("/{run_id}/snapshots", response_model=list[RunSnapshotV1])
def list_snapshots(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[RunSnapshotV1]:
    _authorize_run(db, run_id, actor, "runs.read")
    return [
        RunSnapshotV1.model_validate(snapshot)
        for snapshot in SnapshotService(db).list_snapshots(run_id)
    ]


@router.post(
    "/{run_id}/snapshots",
    response_model=RunSnapshotV1,
    status_code=201,
)
def create_snapshot(
    run_id: str,
    payload: SnapshotCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RunSnapshotV1:
    _authorize_run(db, run_id, actor, "runs.write")
    try:
        snapshot = SnapshotService(db).create(run_id, payload.reason)
        db.commit()
        db.refresh(snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RunSnapshotV1.model_validate(snapshot)


@router.get(
    "/{run_id}/snapshots/{snapshot_id}",
    response_model=RunSnapshotV1,
)
def get_snapshot(
    run_id: str,
    snapshot_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RunSnapshotV1:
    _authorize_run(db, run_id, actor, "runs.read")
    snapshot = SnapshotService(db).get(run_id, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return RunSnapshotV1.model_validate(snapshot)


@router.post(
    "/{run_id}/restore",
    response_model=SnapshotRestoreResultV1,
)
def restore_snapshot(
    run_id: str,
    payload: SnapshotRestoreV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> SnapshotRestoreResultV1:
    _authorize_run(db, run_id, actor, "runs.write")
    try:
        run, restored_step_ids = SnapshotService(db).restore(
            run_id, payload.snapshot_id, payload.reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SnapshotRestoreResultV1(
        run_id=run.id,
        snapshot_id=payload.snapshot_id,
        status=run.status,
        restored_step_ids=restored_step_ids,
    )


@router.post(
    "/{run_id}/replay",
    response_model=ReplayCreatedV1,
    status_code=201,
)
def replay_snapshot(
    run_id: str,
    payload: SnapshotReplayV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ReplayCreatedV1:
    _authorize_run(db, run_id, actor, "runs.execute")
    try:
        replay = SnapshotService(db).replay(
            run_id,
            payload.snapshot_id,
            payload.reason,
            payload.overrides.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ReplayCreatedV1(
        replay_id=replay.id,
        source_run_id=replay.source_run_id,
        source_snapshot_id=replay.source_snapshot_id,
        replay_run_id=replay.replay_run_id,
        replay_reason=replay.replay_reason,
        overrides=replay.overrides,
        created_at=replay.created_at,
    )


@router.get(
    "/{run_id}/comparison",
    response_model=ReplayComparisonV1,
)
def replay_comparison(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ReplayComparisonV1:
    _authorize_run(db, run_id, actor, "runs.read")
    try:
        return ReplayComparisonV1.model_validate(SnapshotService(db).comparison(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/agent-steps", status_code=201)
def create_external_agent_step(
    run_id: str,
    payload: ExternalAgentStepCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize_run(db, run_id, actor, "runs.execute")
    try:
        scheduled = ExternalAgentService(db).schedule(run_id, payload, actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "framework": scheduled.framework,
        "agent_name": scheduled.agent_name,
        "step": RunStepRead.model_validate(scheduled.step),
        "task": scheduled.task,
    }


@router.post("/{run_id}/agent-steps/{step_id}/result")
def submit_external_agent_result(
    run_id: str,
    step_id: str,
    payload: ExternalAgentResultSubmit,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize_run(db, run_id, actor, "runs.execute")
    try:
        submission = ExternalAgentService(db).submit(
            run_id, step_id, payload.result, actor
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "step": RunStepRead.model_validate(submission.step),
        "approval_id": submission.approval_id,
        "recovery": submission.recovery,
    }


@router.get("/{run_id}/recovery", response_model=RecoveryStateV1)
def get_recovery_state(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RecoveryStateV1:
    run = _authorize_run(db, run_id, actor, "runs.read")
    return RecoveryStateV1.model_validate(
        run.recovery_state
        or {
            "status": "IDLE",
            "attempt_count": 0,
        }
    )


@router.post("/{run_id}/recover")
def recover_failed_step(
    run_id: str,
    payload: RecoveryApplyV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize_run(db, run_id, actor, "runs.execute")
    step = RunService(db).get_step(run_id, payload.step_id)
    if step is None:
        raise HTTPException(status_code=404, detail="Run step not found")
    failure = (step.output or {}).get("failure") or (step.output or {}).get(
        "last_failure"
    )
    if not failure:
        raise HTTPException(status_code=409, detail="Run step has no recorded failure")
    try:
        recovered, report, decision = RecoveryService(db).handle(
            run_id,
            step.id,
            failure,
            requested_action=payload.action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "step": RunStepRead.model_validate(recovered),
        "failure": report,
        "recovery": decision,
    }


@router.get("/{run_id}/events")
def list_events(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict]:
    service = RunService(db)
    _authorize_run(db, run_id, actor, "runs.execute")
    return [
        {
            "id": event.id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "actor": event.actor,
            "payload": event.payload,
            "event_hash": event.event_hash,
            "previous_event_hash": event.previous_event_hash,
            "occurred_at": event.occurred_at,
        }
        for event in service.events(run_id)
    ]


@router.post("/{run_id}/execute")
def execute_run(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize_run(db, run_id, actor, "runs.execute")
    try:
        return workflow_backend(db).execute(run_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/cancel", response_model=RunRead)
def cancel_run(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RunRead:
    try:
        _authorize_run(db, run_id, actor, "runs.execute")
        return RunRead.model_validate(RunService(db).cancel(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/resume", response_model=RunRead)
def resume_run(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RunRead:
    try:
        _authorize_run(db, run_id, actor, "runs.execute")
        return RunRead.model_validate(RunService(db).resume(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/steps/{step_id}/retry", response_model=RunStepRead)
def retry_step(
    run_id: str,
    step_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RunStepRead:
    try:
        _authorize_run(db, run_id, actor, "runs.execute")
        return RunStepRead.model_validate(RunService(db).retry_step(run_id, step_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/evidence")
def build_evidence(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize_run(db, run_id, actor, "evidence.build")
    try:
        evidence = EvidenceService(db).build(run_id, actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": evidence.id,
        "path": evidence.path,
        "manifest_hash": evidence.manifest_hash,
    }


@router.post("/{run_id}/evidence/artifacts", status_code=201)
def ingest_evidence_artifact(
    run_id: str,
    payload: EvidenceArtifactIngest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    try:
        _authorize_run(db, run_id, actor, "evidence.build")
        artifact = EvidenceService(db).ingest_artifact(
            run_id, payload.artifact_type, payload.source_path, actor
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": artifact.id,
        "artifact_type": artifact.artifact_type,
        "path": artifact.path,
        "content_hash": artifact.content_hash,
    }


@router.get("/{run_id}/evidence")
def list_evidence(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict]:
    _authorize_run(db, run_id, actor, "evidence.read")
    packs = db.query(EvidencePack).filter(EvidencePack.run_id == run_id).all()
    return [
        {
            "id": pack.id,
            "path": pack.path,
            "manifest_hash": pack.manifest_hash,
            "created_at": pack.created_at,
        }
        for pack in packs
    ]


@router.get("/{run_id}/evidence/{evidence_id}/manifest")
def get_evidence_manifest(
    run_id: str,
    evidence_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize_run(db, run_id, actor, "evidence.read")
    try:
        return EvidenceService(db).manifest(run_id, evidence_id, actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{run_id}/evidence/{evidence_id}/verify",
    response_model=VerificationResultV1,
)
def verify_evidence(
    run_id: str,
    evidence_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> VerificationResultV1:
    _authorize_run(db, run_id, actor, "evidence.read")
    try:
        return EvidenceService(db).verify(run_id, evidence_id, actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{run_id}/evidence/verify-chain",
    response_model=VerificationResultV1,
)
def verify_evidence_chain(
    run_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> VerificationResultV1:
    _authorize_run(db, run_id, actor, "evidence.read")
    return EvidenceService(db).verify_chain(run_id, actor)
