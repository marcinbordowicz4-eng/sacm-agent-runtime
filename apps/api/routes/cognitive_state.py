from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from sacm.core.auth_service import require_authenticated_actor
from sacm.core.cognitive_commit_queue_service import CognitiveCommitQueueService
from sacm.core.cognitive_state_service import (
    CognitiveStateError,
    CognitiveStateNotFoundError,
    CognitiveStateService,
)
from sacm.core.tenancy_service import AuthorizationError, TenancyService
from sacm.infrastructure.db.session import get_db
from sacm.schemas.cognitive_state import (
    AgentHandoffCreateV1,
    AgentHandoffV1,
    CognitiveContextV1,
    CognitiveDeliveryPassportV1,
    CognitiveEventCreateV1,
    CognitiveEventV1,
    CognitiveRelationCreateV1,
    CognitiveRelationV1,
    CognitiveSnapshotCreateV1,
    CognitiveSnapshotV1,
    CommitIngestV1,
    ContextReconstructV1,
    ExplainFileV1,
    GitCommitAnalyzeV1,
    GitCommitJobV1,
    ImpactAnalysisRequestV1,
    ImpactAnalysisV1,
)

router = APIRouter()


def _authorize(db: Session, project_id: str, actor: str, permission: str) -> None:
    try:
        TenancyService(db).require_project_permission(
            project_id,
            actor,
            permission,
            resource_type="cognitive_state",
            resource_id=project_id,
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _service_error(exc: CognitiveStateError) -> HTTPException:
    status = 404 if isinstance(exc, CognitiveStateNotFoundError) else 409
    return HTTPException(status_code=status, detail=str(exc))


@router.post("/projects/{project_id}/cognitive/events", response_model=CognitiveEventV1, status_code=201)
def record_event(
    project_id: str,
    payload: CognitiveEventCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> CognitiveEventV1:
    _authorize(db, project_id, actor, "tasks.write")
    try:
        event = CognitiveStateService(db).record_event(project_id, payload)
        return CognitiveStateService._event_read(event)
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.post("/projects/{project_id}/cognitive/relations", response_model=CognitiveRelationV1, status_code=201)
def add_relation(
    project_id: str,
    payload: CognitiveRelationCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> CognitiveRelationV1:
    _authorize(db, project_id, actor, "tasks.write")
    try:
        relation = CognitiveStateService(db).add_relation(project_id, payload, actor_id=actor)
        return CognitiveRelationV1(
            id=relation.id,
            project_id=relation.project_id,
            source_type=relation.source_type,
            source_id=relation.source_id,
            target_type=relation.target_type,
            target_id=relation.target_id,
            relation=relation.relation,
            commit_hash=relation.commit_hash,
            metadata=relation.metadata_,
            created_event_id=relation.created_event_id,
            valid_from=relation.valid_from,
            valid_until=relation.valid_until,
        )
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.post("/projects/{project_id}/cognitive/commits", status_code=201)
def ingest_commit(
    project_id: str,
    payload: CommitIngestV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize(db, project_id, actor, "tasks.write")
    try:
        return CognitiveStateService(db).ingest_commit(project_id, payload)
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.post("/projects/{project_id}/cognitive/git/commit", status_code=201)
def analyze_git_commit(
    project_id: str,
    payload: GitCommitAnalyzeV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize(db, project_id, actor, "tasks.write")
    try:
        return CognitiveStateService(db).analyze_and_ingest_git_commit(
            project_id, payload
        )
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.post(
    "/projects/{project_id}/cognitive/git/commit-jobs",
    response_model=GitCommitJobV1,
    status_code=202,
)
def enqueue_git_commit(
    project_id: str,
    payload: GitCommitAnalyzeV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> GitCommitJobV1:
    _authorize(db, project_id, actor, "tasks.write")
    try:
        job = CognitiveCommitQueueService(db).submit(project_id, payload)
        return CognitiveCommitQueueService.read(job)
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.post(
    "/projects/{project_id}/cognitive/handoffs",
    response_model=AgentHandoffV1,
    status_code=201,
)
def handoff(
    project_id: str,
    payload: AgentHandoffCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> AgentHandoffV1:
    _authorize(db, project_id, actor, "tasks.write")
    try:
        return CognitiveStateService(db).handoff(project_id, payload)
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.post(
    "/projects/{project_id}/cognitive/snapshots",
    response_model=CognitiveSnapshotV1,
    status_code=201,
)
def create_snapshot(
    project_id: str,
    payload: CognitiveSnapshotCreateV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> CognitiveSnapshotV1:
    _authorize(db, project_id, actor, "tasks.write")
    try:
        snapshot = CognitiveStateService(db).create_snapshot(
            project_id,
            commit_hash=payload.commit_hash,
            snapshot_type=payload.snapshot_type,
            reason=payload.reason,
        )
        result = CognitiveStateService._snapshot_read(snapshot)
        assert result is not None
        return result
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.get("/projects/{project_id}/cognitive/snapshots/{snapshot_id}", response_model=CognitiveSnapshotV1)
def get_snapshot(
    project_id: str,
    snapshot_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> CognitiveSnapshotV1:
    _authorize(db, project_id, actor, "tasks.read")
    snapshot = CognitiveStateService(db).snapshot(project_id, snapshot_id=snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Cognitive snapshot not found.")
    result = CognitiveStateService._snapshot_read(snapshot)
    assert result is not None
    return result


@router.get(
    "/projects/{project_id}/cognitive/deliveries/{delivery_id}/passport",
    response_model=CognitiveDeliveryPassportV1,
)
def delivery_passport(
    project_id: str,
    delivery_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> CognitiveDeliveryPassportV1:
    _authorize(db, project_id, actor, "tasks.read")
    try:
        return CognitiveStateService(db).delivery_passport(project_id, delivery_id)
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.get("/projects/{project_id}/cognitive/state")
def state_at_commit(
    project_id: str,
    commit: str = Query(min_length=7, max_length=128),
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> dict:
    _authorize(db, project_id, actor, "tasks.read")
    try:
        return CognitiveStateService(db).state_at_commit(project_id, commit)
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.post("/projects/{project_id}/cognitive/context/reconstruct", response_model=CognitiveContextV1)
def reconstruct_context(
    project_id: str,
    payload: ContextReconstructV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> CognitiveContextV1:
    _authorize(db, project_id, actor, "tasks.read")
    try:
        return CognitiveStateService(db).reconstruct_context(
            project_id,
            agent_id=payload.agent_id,
            task=payload.task,
            commit_hash=payload.commit_hash,
            snapshot_id=payload.snapshot_id,
            max_items=payload.max_items,
        )
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.get("/projects/{project_id}/cognitive/explain/file", response_model=ExplainFileV1)
def explain_file(
    project_id: str,
    path: str = Query(min_length=1, max_length=2_000),
    commit: str | None = Query(default=None, min_length=7, max_length=128),
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ExplainFileV1:
    _authorize(db, project_id, actor, "tasks.read")
    try:
        return CognitiveStateService(db).explain_file(project_id, path, commit_hash=commit)
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc


@router.post("/projects/{project_id}/cognitive/impact-analysis", response_model=ImpactAnalysisV1)
def impact_analysis(
    project_id: str,
    payload: ImpactAnalysisRequestV1,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ImpactAnalysisV1:
    _authorize(db, project_id, actor, "tasks.read")
    try:
        return CognitiveStateService(db).impact_analysis(
            project_id, payload.file, commit_hash=payload.commit_hash
        )
    except CognitiveStateError as exc:
        raise _service_error(exc) from exc
