
import pytest

from sacm import mcp_server


def test_request_headers_use_local_actor_by_default(monkeypatch):
    monkeypatch.delenv("SACM_API_TOKEN", raising=False)
    monkeypatch.delenv("SACM_ACTOR_ID", raising=False)

    assert mcp_server._request_headers() == {"X-SACM-Actor": "copilot-cli"}


def test_request_headers_use_api_token_when_configured(monkeypatch):
    monkeypatch.setenv("SACM_ACTOR_ID", "local-user")
    monkeypatch.setenv("SACM_API_TOKEN", "test-token")

    assert mcp_server._request_headers() == {"Authorization": "Bearer test-token"}


def test_advise_creates_task_and_compiles_context(monkeypatch):
    calls = []

    def request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/tasks":
            return {"id": "task-1", "status": "pending"}
        return {"task_id": "task-1", "goal": "Complete task: Fix bug"}

    monkeypatch.setattr(mcp_server, "_request", request)

    result = mcp_server.sacm_advise("Fix bug")

    assert result["task_id"] == "task-1"
    assert calls == [
        (
            "POST",
            "/tasks",
            {
                "title": "Fix bug",
                "description": "Fix bug",
                "target_repo_path": None,
            },
        ),
        (
            "POST",
            "/context/compile",
            {"task_id": "task-1", "agent_name": "ClaudeReasoner"},
        ),
    ]


def test_repository_path_accepts_any_existing_directory(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()

    assert mcp_server._repository_path(str(repository)) == str(repository.resolve())


def test_repository_path_rejects_missing_directory(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        mcp_server._repository_path(str(tmp_path / "missing"))


def test_agent_lifecycle_tools_call_expected_endpoints(monkeypatch):
    calls = []

    def request(method, path, payload=None):
        calls.append((method, path, payload))
        return {"path": path}

    monkeypatch.setattr(mcp_server, "_request", request)

    assert mcp_server.sacm_run_agents("task-1") == {"path": "/tasks/task-1/run"}
    assert mcp_server.sacm_get_task("task-1") == {"path": "/tasks/task-1"}
    assert mcp_server.sacm_get_events("task-1") == {"path": "/tasks/task-1/events"}
    assert mcp_server.sacm_get_memory("task-1") == {"path": "/tasks/task-1/memory"}
    assert mcp_server.sacm_add_memory("task-1", "Found the root cause") == {
        "path": "/memory/add"
    }
    assert calls == [
        ("POST", "/tasks/task-1/run", None),
        ("GET", "/tasks/task-1", None),
        ("GET", "/tasks/task-1/events", None),
        ("GET", "/tasks/task-1/memory", None),
        (
            "POST",
            "/memory/add",
            {
                "task_id": "task-1",
                "content": "Found the root cause",
                "source_type": "mcp",
                "importance": 0.6,
            },
        ),
    ]
