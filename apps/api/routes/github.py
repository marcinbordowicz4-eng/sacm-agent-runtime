from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from sacm.adapters.github_adapter import GitHubAdapter
from sacm.core.auth_service import (
    require_authenticated_actor,
    require_direct_action_api_enabled,
)
from sacm.core.github_webhook_service import GitHubWebhookService
from sacm.infrastructure.db.session import get_db

router = APIRouter()


class GitHubRepositoryRequest(BaseModel):
    repo_path: str


class CreateIssueRequest(GitHubRepositoryRequest):
    title: str
    body: str


class PushBranchRequest(GitHubRepositoryRequest):
    branch_name: str


class CreatePullRequestRequest(GitHubRepositoryRequest):
    title: str
    body: str
    head: str
    base: str = "main"


class MergePullRequestRequest(GitHubRepositoryRequest):
    confirmation: Literal["merge-when-green"]


def _require_success(result: dict) -> dict:
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"] or result["stdout"])
    return result


def _require_sacm_branch(branch_name: str) -> None:
    if not branch_name.startswith("sacm/"):
        raise HTTPException(
            status_code=403,
            detail="SACM may deliver only through a sacm/ branch.",
        )


@router.post("/issues")
def create_issue(
    payload: CreateIssueRequest,
    _: str = Depends(require_authenticated_actor),
    __: None = Depends(require_direct_action_api_enabled),
) -> dict:
    return _require_success(
        GitHubAdapter(payload.repo_path).create_issue(payload.title, payload.body)
    )


@router.post("/branches/push")
def push_branch(
    payload: PushBranchRequest,
    _: str = Depends(require_authenticated_actor),
    __: None = Depends(require_direct_action_api_enabled),
) -> dict:
    _require_sacm_branch(payload.branch_name)
    return _require_success(GitHubAdapter(payload.repo_path).push_branch(payload.branch_name))


@router.post("/pull-requests")
def open_pull_request(
    payload: CreatePullRequestRequest,
    _: str = Depends(require_authenticated_actor),
    __: None = Depends(require_direct_action_api_enabled),
) -> dict:
    _require_sacm_branch(payload.head)
    return _require_success(
        GitHubAdapter(payload.repo_path).open_pull_request(
            payload.title, payload.body, payload.head, payload.base
        )
    )


@router.get("/pull-requests/{pull_request_number}/comments")
def review_comments(
    pull_request_number: int,
    repo_path: str,
    _: str = Depends(require_authenticated_actor),
    __: None = Depends(require_direct_action_api_enabled),
) -> list[dict]:
    try:
        return GitHubAdapter(repo_path).read_review_comments(pull_request_number)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pull-requests/{pull_request_number}/merge")
def merge_when_green(
    pull_request_number: int, payload: MergePullRequestRequest
) -> dict:
    raise HTTPException(
        status_code=403,
        detail=(
            "SACM never merges pull requests through its API. "
            "Use GitHub branch protection and a human-authorized merge."
        ),
    )


@router.post("/webhooks", status_code=202)
async def github_webhook(request: Request, db=Depends(get_db)) -> dict:
    body = await request.body()
    service = GitHubWebhookService(db)
    if not service.verify_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature.")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Webhook body must be JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be an object.")
    delivery_id = request.headers.get("X-GitHub-Delivery")
    if not delivery_id:
        raise HTTPException(status_code=400, detail="Missing GitHub delivery ID.")
    run_id = service.create_run(payload, delivery_id=delivery_id)
    return {"accepted": True, "run_id": run_id}
