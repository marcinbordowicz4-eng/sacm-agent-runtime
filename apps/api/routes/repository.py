from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sacm.adapters.repository_adapter import RepositoryAdapter
from sacm.core.repository_audit_service import RepositoryAuditService
from sacm.infrastructure.db.session import get_db

router = APIRouter()


class RepositoryPathRequest(BaseModel):
    repo_path: str
    task_id: str | None = None


class CreateWorktreeRequest(RepositoryPathRequest):
    branch_name: str


class ApplyPatchRequest(RepositoryPathRequest):
    patch: str


class RunTestsRequest(RepositoryPathRequest):
    command: str


@router.post("/analyze")
def analyze_repository(
    payload: RepositoryPathRequest, db: Session = Depends(get_db)
) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    files = adapter.list_files()
    RepositoryAuditService(db).record(
        payload.task_id,
        "analyzed",
        str(adapter.repo_path),
        {"file_count": len(files)},
    )
    return {"repo_path": str(adapter.repo_path), "files": files}


@router.post("/create-worktree")
def create_worktree(
    payload: CreateWorktreeRequest, db: Session = Depends(get_db)
) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    worktree_path = adapter.create_worktree(payload.branch_name)
    RepositoryAuditService(db).record(
        payload.task_id,
        "worktree_created",
        str(adapter.repo_path),
        {
            "branch_name": payload.branch_name,
            "worktree_path": worktree_path,
        },
        memory_summary=(
            f"Created or reused worktree {worktree_path} on "
            f"branch {payload.branch_name}."
        ),
    )
    return {"worktree_path": worktree_path}


@router.post("/apply-patch")
def apply_patch(
    payload: ApplyPatchRequest, db: Session = Depends(get_db)
) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    adapter.apply_patch(payload.patch)
    diff = adapter.get_diff()
    summary = RepositoryAuditService.content_summary(payload.patch)
    changed_files = RepositoryAuditService.changed_files(diff)
    RepositoryAuditService(db).record(
        payload.task_id,
        "patch_applied",
        str(adapter.repo_path),
        {
            **summary,
            "changed_files": changed_files,
            "diff_sha256": RepositoryAuditService.content_summary(diff)["sha256"],
        },
        memory_summary=(
            f"Applied implementation patch {summary['sha256'][:12]} affecting "
            f"{', '.join(changed_files) if changed_files else 'no detected files'}."
        ),
    )
    return {"status": "applied"}


@router.post("/run-tests")
def run_tests(payload: RunTestsRequest, db: Session = Depends(get_db)) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    result = adapter.run_command(payload.command)
    RepositoryAuditService(db).record(
        payload.task_id,
        "verification_completed",
        str(adapter.repo_path),
        {
            "command": payload.command,
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "passed": result["returncode"] == 0,
        },
        memory_summary=(
            f"Verification `{payload.command}` "
            f"{'passed' if result['returncode'] == 0 else 'failed'} "
            f"with exit code {result['returncode']}."
        ),
    )
    return result


@router.post("/diff")
def diff_repository(
    payload: RepositoryPathRequest, db: Session = Depends(get_db)
) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    diff = adapter.get_diff()
    summary = RepositoryAuditService.content_summary(diff)
    changed_files = RepositoryAuditService.changed_files(diff)
    RepositoryAuditService(db).record(
        payload.task_id,
        "diff_captured",
        str(adapter.repo_path),
        {**summary, "changed_files": changed_files},
        memory_summary=(
            f"Captured implementation diff {summary['sha256'][:12]} affecting "
            f"{', '.join(changed_files) if changed_files else 'no detected files'}."
        ),
    )
    return {
        "diff": diff,
        "sha256": summary["sha256"],
        "changed_files": changed_files,
    }
