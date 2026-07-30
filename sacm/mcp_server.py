"""Local stdio MCP bridge for the SACM API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

_API_URL = os.getenv("SACM_API_URL", "http://127.0.0.1:8000").rstrip("/")


mcp = FastMCP(
    "SACM Agent Runtime",
    instructions=(
        "Use sacm_advise before implementation to retrieve a persistent task "
        "briefing. Use sacm_run_agents to run the SACM agent workflow, and "
        "inspect its task, events, and memory before continuing. Then use the "
        "explicit patch, verification, and diff tools to carry out the agreed "
        "work."
    ),
)


def _request_headers() -> dict[str, str]:
    api_token = os.getenv("SACM_API_TOKEN")
    if api_token:
        return {"Authorization": f"Bearer {api_token}"}
    return {"X-SACM-Actor": os.getenv("SACM_ACTOR_ID", "copilot-cli")}


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    response = httpx.request(
        method,
        f"{_API_URL}{path}",
        json=payload,
        headers=_request_headers(),
        timeout=120,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            body = response.json()
            detail = body.get("detail", body)
            error_code = body.get("error_code")
        except ValueError:
            detail = response.text.strip() or response.reason_phrase
            error_code = None
        label = f" [{error_code}]" if error_code else ""
        raise RuntimeError(
            f"SACM API {response.status_code}{label} for {method} {path}: {detail}"
        ) from exc
    return response.json()


def _repository_path(repository_path: str) -> str:
    path = Path(repository_path).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Repository path does not exist: {path}")
    return str(path)


@mcp.tool()
def sacm_advise(task: str, repository_path: str | None = None) -> dict[str, Any]:
    """Create a task and return SACM's persistent context briefing for planning."""
    target_repo_path = _repository_path(repository_path) if repository_path else None
    created_task = _request(
        "POST",
        "/tasks",
        {
            "title": task[:80],
            "description": task,
            "target_repo_path": target_repo_path,
        },
    )
    briefing = _request(
        "POST",
        "/context/compile",
        {
            "task_id": created_task["id"],
            "agent_name": "ClaudeReasoner",
        },
    )
    return {
        "task_id": created_task["id"],
        "status": created_task["status"],
        "briefing": briefing,
    }


@mcp.tool()
def sacm_run_agents(task_id: str) -> dict[str, Any]:
    """Run SACM's registered agent workflow for an existing task."""
    return _request("POST", f"/tasks/{task_id}/run")


@mcp.tool()
def sacm_get_task(task_id: str) -> dict[str, Any]:
    """Return an existing task and its current lifecycle status."""
    return _request("GET", f"/tasks/{task_id}")


@mcp.tool()
def sacm_get_events(task_id: str) -> list[dict[str, Any]]:
    """Return the recent agent events recorded for a task."""
    return _request("GET", f"/tasks/{task_id}/events")


@mcp.tool()
def sacm_get_memory(task_id: str) -> list[dict[str, Any]]:
    """Return the persistent memory associated with a task."""
    return _request("GET", f"/tasks/{task_id}/memory")


@mcp.tool()
def sacm_add_memory(
    task_id: str,
    content: str,
    source_type: str = "mcp",
    importance: float = 0.6,
) -> dict[str, Any]:
    """Persist a finding so SACM agents can use it in later task steps."""
    return _request(
        "POST",
        "/memory/add",
        {
            "task_id": task_id,
            "content": content,
            "source_type": source_type,
            "importance": importance,
        },
    )


@mcp.tool()
def sacm_apply_patch(
    repository_path: str, patch: str, task_id: str | None = None
) -> dict[str, Any]:
    """Apply a patch and persist its implementation context for a SACM task."""
    return _request(
        "POST",
        "/repository/apply-patch",
        {
            "repo_path": _repository_path(repository_path),
            "patch": patch,
            "task_id": task_id,
        },
    )


@mcp.tool()
def sacm_run_verification(
    repository_path: str, command: str, task_id: str | None = None
) -> dict[str, Any]:
    """Run verification and persist its result for a SACM task."""
    return _request(
        "POST",
        "/repository/run-tests",
        {
            "repo_path": _repository_path(repository_path),
            "command": command,
            "task_id": task_id,
        },
    )


@mcp.tool()
def sacm_get_diff(
    repository_path: str, task_id: str | None = None
) -> dict[str, Any]:
    """Return the Git diff and persist its implementation context."""
    return _request(
        "POST",
        "/repository/diff",
        {
            "repo_path": _repository_path(repository_path),
            "task_id": task_id,
        },
    )


def main() -> None:
    mcp.run(transport="stdio")
