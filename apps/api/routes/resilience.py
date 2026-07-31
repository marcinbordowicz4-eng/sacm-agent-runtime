from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from sacm.core.auth_service import require_authenticated_actor
from sacm.core.execution_plane_service import ExecutionConflict, ExecutionPlaneService
from sacm.core.resilience_service import (
    BackupService,
    OperationalHealthService,
    SLOService,
)
from sacm.core.tenancy_service import AuthorizationError, TenancyService
from sacm.infrastructure.db.models import ExecutionJob
from sacm.infrastructure.db.session import get_db
from sacm.schemas.execution_plane import ExecutionJobRead
from sacm.schemas.resilience import (
    BackupCreate,
    BackupRead,
    DeadLetterRequeueRequest,
    DRDrillRead,
    OperationalHealthRead,
    RecoveryReport,
    RecoveryRequest,
    RestoreVerificationRequest,
    SLOContractRead,
    SLOContractUpsert,
    SLOEvaluationRead,
)

router = APIRouter(dependencies=[Depends(require_authenticated_actor)])


def _error(exc: Exception) -> None:
    if isinstance(exc, (AuthorizationError, PermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ValueError) and "not found" in str(exc).lower():
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/backups", response_model=BackupRead, status_code=201)
def create_backup(
    payload: BackupCreate,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> BackupRead:
    try:
        record = BackupService(db).create(payload, actor)
    except (AuthorizationError, PermissionError, ValueError) as exc:
        _error(exc)
    return BackupRead.model_validate(record)


@router.get("/backups", response_model=list[BackupRead])
def list_backups(
    organization_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[BackupRead]:
    try:
        records = BackupService(db).list(actor, organization_id)
    except (AuthorizationError, PermissionError) as exc:
        _error(exc)
    return [BackupRead.model_validate(record) for record in records]


@router.post("/backups/{backup_id}/execute", response_model=BackupRead)
def execute_backup(
    backup_id: str,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> BackupRead:
    try:
        record = BackupService(db).execute(backup_id, actor)
    except (AuthorizationError, PermissionError, ValueError) as exc:
        _error(exc)
    return BackupRead.model_validate(record)


@router.post("/backups/{backup_id}/restore-verification", response_model=DRDrillRead)
def verify_restore(
    backup_id: str,
    payload: RestoreVerificationRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> DRDrillRead:
    try:
        drill = BackupService(db).verify_restore(
            backup_id,
            actor,
            destructive_restore=payload.destructive_restore,
            target_database=payload.target_database,
            guard_token=payload.guard_token,
            keep_isolated_database=payload.keep_isolated_database,
        )
    except (AuthorizationError, PermissionError, ValueError) as exc:
        _error(exc)
    return DRDrillRead.model_validate(drill)


@router.post("/slo/defaults", response_model=list[SLOContractRead])
def ensure_slo_defaults(
    organization_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[SLOContractRead]:
    try:
        contracts = SLOService(db).ensure_defaults(actor, organization_id)
    except (AuthorizationError, PermissionError) as exc:
        _error(exc)
    return [SLOContractRead.model_validate(item) for item in contracts]


@router.put("/slo/contracts", response_model=SLOContractRead)
def upsert_slo_contract(
    payload: SLOContractUpsert,
    organization_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> SLOContractRead:
    try:
        contract = SLOService(db).upsert(actor, payload, organization_id)
    except (AuthorizationError, PermissionError) as exc:
        _error(exc)
    return SLOContractRead.model_validate(contract)


@router.get("/slo/contracts", response_model=list[SLOContractRead])
def list_slo_contracts(
    organization_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[SLOContractRead]:
    try:
        contracts = SLOService(db).list_contracts(actor, organization_id)
    except (AuthorizationError, PermissionError) as exc:
        _error(exc)
    return [SLOContractRead.model_validate(item) for item in contracts]


@router.post("/slo/evaluate", response_model=list[SLOEvaluationRead])
def evaluate_slos(
    organization_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[SLOEvaluationRead]:
    try:
        evaluations = SLOService(db).evaluate(actor, organization_id)
    except (AuthorizationError, PermissionError) as exc:
        _error(exc)
    return [SLOEvaluationRead.model_validate(item) for item in evaluations]


@router.get("/operations/health", response_model=OperationalHealthRead)
def operational_health(
    organization_id: str | None = None,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> OperationalHealthRead:
    try:
        BackupService(db)._authorize(organization_id, actor, "resilience.read")
        result = OperationalHealthService(db).inspect(organization_id)
    except (AuthorizationError, PermissionError) as exc:
        _error(exc)
    return OperationalHealthRead.model_validate(result)


@router.post("/operations/execution/recover", response_model=RecoveryReport)
def recover_execution_jobs(
    payload: RecoveryRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> RecoveryReport:
    try:
        BackupService(db)._authorize(
            payload.organization_id, actor, "operations.manage"
        )
        result = ExecutionPlaneService(db).recover_orphaned(
            organization_id=payload.organization_id
        )
    except (AuthorizationError, PermissionError) as exc:
        _error(exc)
    return RecoveryReport.model_validate(result)


@router.get("/operations/execution/jobs", response_model=list[ExecutionJobRead])
def list_execution_jobs(
    organization_id: str | None = None,
    state: str | None = None,
    limit: int = 200,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> list[ExecutionJobRead]:
    try:
        BackupService(db)._authorize(organization_id, actor, "operations.manage")
        jobs = ExecutionPlaneService(db).list_jobs(
            organization_id=organization_id, state=state, limit=limit
        )
    except (AuthorizationError, PermissionError) as exc:
        _error(exc)
    return [ExecutionJobRead.model_validate(job) for job in jobs]


@router.post(
    "/operations/execution/jobs/{job_id}/requeue",
    response_model=ExecutionJobRead,
)
def requeue_dead_letter(
    job_id: str,
    payload: DeadLetterRequeueRequest,
    actor: str = Depends(require_authenticated_actor),
    db: Session = Depends(get_db),
) -> ExecutionJobRead:
    try:
        job = db.get(ExecutionJob, job_id)
        if job is None:
            raise ValueError("Execution job not found.")
        if job.organization_id:
            TenancyService(db).require_permission(
                job.organization_id,
                actor,
                "operations.manage",
                resource_type="execution_job",
                resource_id=job.id,
            )
        else:
            BackupService(db)._authorize(None, actor, "operations.manage")
        job = ExecutionPlaneService(db).requeue_dead_letter(
            job_id,
            reason=payload.reason,
            reset_attempts=payload.reset_attempts,
        )
    except (
        AuthorizationError,
        PermissionError,
        ValueError,
        ExecutionConflict,
    ) as exc:
        _error(exc)
    return ExecutionJobRead.model_validate(job)
