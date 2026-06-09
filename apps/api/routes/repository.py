from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sacm.adapters.repository_adapter import RepositoryAdapter

router = APIRouter()


class RepositoryPathRequest(BaseModel):
    repo_path: str


class CreateWorktreeRequest(RepositoryPathRequest):
    branch_name: str


class ApplyPatchRequest(RepositoryPathRequest):
    patch: str


class RunTestsRequest(RepositoryPathRequest):
    command: str


@router.post("/analyze")
def analyze_repository(payload: RepositoryPathRequest) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    return {"repo_path": payload.repo_path, "files": adapter.list_files()}


@router.post("/create-worktree")
def create_worktree(payload: CreateWorktreeRequest) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    return {"worktree_path": adapter.create_worktree(payload.branch_name)}


@router.post("/apply-patch")
def apply_patch(payload: ApplyPatchRequest) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    try:
        adapter.apply_patch(payload.patch)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "applied"}


@router.post("/run-tests")
def run_tests(payload: RunTestsRequest) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    return adapter.run_command(payload.command)


@router.post("/diff")
def diff_repository(payload: RepositoryPathRequest) -> dict:
    adapter = RepositoryAdapter(payload.repo_path)
    return {"diff": adapter.get_diff()}
