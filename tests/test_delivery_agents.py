import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from sacm.adapters.codex_executor_adapter import CodexExecutorAdapter
from sacm.adapters.github_adapter import GitHubAdapter
from sacm.adapters.openai_agents_adapter import OpenAIAgentsAdapter
from sacm.agents.codex_executor import CodexExecutorAgent
from sacm.agents.eas_workflow import EASWorkflowAgent
from sacm.agents.mobile_e2e import MobileE2EAgent
from sacm.agents.openai_agents_executor import OpenAIAgentsExecutorAgent
from sacm.agents.security_delivery import SecurityDeliveryAgent
from sacm.schemas.context import AgentContext


def _context(repo_path: Path | None = None) -> AgentContext:
    return AgentContext(
        task_id="task-1",
        task="Implement a change",
        goal="Implement a change safely",
        current_state="testing",
        target_repo_path=str(repo_path) if repo_path else None,
    )


def test_codex_executor_uses_safe_task_branch_name():
    assert CodexExecutorAdapter._branch_name("task id/1") == "sacm/task-id-1"


def test_github_adapter_refuses_non_sacm_delivery_branch(tmp_path):
    adapter = GitHubAdapter(str(tmp_path))

    with pytest.raises(ValueError, match="sacm/ branches"):
        adapter.push_branch("main")
    with pytest.raises(ValueError, match="sacm/ branches"):
        adapter.open_pull_request("Title", "Body", "feature/change")


def test_github_adapter_never_merges_pull_requests(tmp_path):
    result = GitHubAdapter(str(tmp_path)).merge_when_green(1)

    assert result["returncode"] != 0
    assert "does not merge" in result["stderr"]


def test_codex_executor_parses_json_lines_only():
    assert CodexExecutorAdapter._json_events('plain\n{"type":"item.completed"}\n') == [
        {"type": "item.completed"}
    ]


def test_codex_executor_prepends_repository_dependencies_to_path(
    monkeypatch, tmp_path
):
    recorded = {}

    def fake_run(command, **kwargs):
        recorded.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    dependency_bin = tmp_path / "node_modules" / ".bin"
    CodexExecutorAdapter._run(
        ["npm", "run", "test:all"],
        str(tmp_path),
        600,
        path_prefix=dependency_bin,
    )

    assert recorded["env"]["PATH"].split(":")[0] == str(dependency_bin)


def test_codex_verification_retries_resource_failure_in_band(monkeypatch, tmp_path):
    adapter = CodexExecutorAdapter(str(tmp_path))
    results = iter(
        [
            {
                "returncode": 137,
                "stdout": "",
                "stderr": "worker received SIGKILL",
                "events": [],
                "duration_ms": 10,
            },
            {
                "returncode": 0,
                "stdout": "passed",
                "stderr": "",
                "events": [],
                "duration_ms": 20,
            },
        ]
    )
    commands = []

    def fake_run(command, *args, **kwargs):
        commands.append(command)
        return next(results)

    monkeypatch.setattr(adapter, "_run", fake_run)

    result = adapter._run_verification(
        "npx jest",
        str(tmp_path),
        tmp_path / "node_modules" / ".bin",
    )

    assert commands == [["npx", "jest"], ["npx", "jest", "--runInBand"]]
    assert result["returncode"] == 0
    assert result["retry_evidence"]["original"]["returncode"] == 137


def test_codex_executor_extracts_only_provider_reported_usage():
    usage = CodexExecutorAdapter._usage_from_events(
        [
            {"data": {"usage": {"input_tokens": 10, "output_tokens": 4}, "model": "x"}},
            {"usage": {"input_tokens": "10", "output_tokens": 4}},
        ]
    )

    assert usage == [
        {
            "provider": "codex",
            "model": "x",
            "operation": "code_execution",
            "input_tokens": 10,
            "output_tokens": 4,
        }
    ]


def test_codex_executor_agent_requires_target_repository():
    result = CodexExecutorAgent().run(_context())

    assert result.next_state_hint == "blocked"


def test_codex_auto_pr_requires_successful_verification(monkeypatch):
    monkeypatch.setenv("SACM_CODEX_AUTO_CREATE_PR", "true")

    action = CodexExecutorAgent._open_pull_request(
        _context(),
        {"verification": [], "worktree_path": ".", "branch_name": "sacm/task"},
        success=True,
    )

    assert action == {
        "type": "PULL_REQUEST",
        "created": False,
        "reason": "Codex and at least one verification command must succeed.",
    }


def test_codex_auto_pr_runs_only_after_successful_verification(monkeypatch):
    class FakeGitHubAdapter:
        def __init__(self, repo_path):
            self.repo_path = repo_path

        def commit_push_and_open_pull_request(self, **kwargs):
            assert kwargs["branch_name"] == "sacm/task"
            assert kwargs["base"] == "main"
            return {"returncode": 0, "stdout": "https://example.test/pr/1", "stderr": ""}

    monkeypatch.setenv("SACM_CODEX_AUTO_CREATE_PR", "true")
    monkeypatch.setattr("sacm.agents.codex_executor.GitHubAdapter", FakeGitHubAdapter)

    action = CodexExecutorAgent._open_pull_request(
        _context(),
        {
            "verification": [{"returncode": 0}],
            "worktree_path": "/worktree",
            "branch_name": "sacm/task",
        },
        success=True,
    )

    assert action["created"] is True
    assert action["type"] == "PULL_REQUEST"


def test_openai_agents_adapter_uses_private_tracing(monkeypatch):
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent"] = kwargs

    class FakeRunConfig:
        def __init__(self, **kwargs):
            captured["run_config"] = kwargs

    class FakeRunner:
        @staticmethod
        def run_sync(*args, **kwargs):
            captured["run_sync"] = kwargs
            return SimpleNamespace(
                final_output="Plan",
                context_wrapper=SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=9, output_tokens=3)
                ),
            )

    monkeypatch.setitem(
        sys.modules,
        "agents",
        SimpleNamespace(
            Agent=FakeAgent,
            RunConfig=FakeRunConfig,
            Runner=FakeRunner,
            trace=lambda *args, **kwargs: nullcontext(),
        ),
    )
    monkeypatch.setenv("SACM_OPENAI_AGENTS_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SACM_OPENAI_AGENTS_MODEL", "gpt-5")

    result = OpenAIAgentsAdapter().run(_context())

    assert result["usage"]["input_tokens"] == 9
    assert result["usage"]["output_tokens"] == 3
    assert captured["run_config"]["trace_include_sensitive_data"] is False


def test_eas_agent_reports_missing_target_configuration(tmp_path):
    result = EASWorkflowAgent().run(_context(tmp_path))

    assert result.next_state_hint == "planning"
    assert "missing" in result.summary


def test_mobile_e2e_agent_does_not_run_without_explicit_enablement(
    tmp_path, monkeypatch
):
    flow_dir = tmp_path / ".maestro"
    flow_dir.mkdir()
    (flow_dir / "smoke.yaml").write_text("appId: com.example.app\n---\n- launchApp\n")
    monkeypatch.setenv("SACM_RUN_MOBILE_E2E", "false")

    result = MobileE2EAgent().run(_context(tmp_path))

    assert result.actions[0]["executed"] is False
    assert result.actions[0]["passed"] is False


def test_security_delivery_agent_reports_missing_workflows(tmp_path):
    result = SecurityDeliveryAgent().run(_context(tmp_path))

    assert result.next_state_hint == "planning"
    assert "incomplete" in result.summary


def test_openai_agents_executor_is_inactive_by_default(monkeypatch):
    monkeypatch.setenv("SACM_OPENAI_AGENTS_ENABLED", "false")

    result = OpenAIAgentsExecutorAgent().run(_context())

    assert result.confidence == 0.0
    assert "disabled" in result.summary
