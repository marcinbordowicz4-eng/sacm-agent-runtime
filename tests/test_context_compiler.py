from unittest.mock import MagicMock

import pytest

from sacm.agents.claude_reasoner import ClaudeReasonerAgent
from sacm.core.context_compiler import ContextCompiler
from sacm.core.repository_config import RepositoryConfigError, load_repository_config


def make_task():
    task = MagicMock()
    task.id = "task-1"
    task.title = "Fix bug"
    task.description = "Fix the failing test"
    task.status = "debugging"
    task.target_repo_path = None
    return task


def test_compile_returns_agent_context():
    compiler = ContextCompiler()
    task = make_task()
    agent = ClaudeReasonerAgent()
    ctx = compiler.compile(task=task, agent=agent, history=[], memory=[])
    assert ctx.task_id == "task-1"
    assert "Fix the failing test" in ctx.task
    assert ctx.current_state == "debugging"
    assert ctx.goal == "Complete task: Fix the failing test"


def test_compile_includes_memory():
    compiler = ContextCompiler()
    task = make_task()
    agent = ClaudeReasonerAgent()
    mem = MagicMock()
    mem.content = "Important memory chunk"
    ctx = compiler.compile(task=task, agent=agent, history=[], memory=[mem])
    assert "Important memory chunk" in ctx.relevant_memory


def test_compile_caps_context_to_token_budget():
    compiler = ContextCompiler(token_budget=20)
    task = make_task()
    task.description = "x" * 200
    agent = ClaudeReasonerAgent()
    mem = MagicMock()
    mem.content = "y" * 200

    ctx = compiler.compile(task=task, agent=agent, history=[], memory=[mem])

    assert len(ctx.task) <= 80
    assert ctx.relevant_memory == []


def test_compile_loads_verified_commands_and_constraints_from_repository_config(tmp_path):
    (tmp_path / ".sacm.yaml").write_text(
        """
version: sacm/v1
commands:
  build: npm run typecheck
  test: npm test -- --runInBand
constraints:
  - Never write to main.
""",
        encoding="utf-8",
    )
    task = make_task()
    task.target_repo_path = str(tmp_path)

    context = ContextCompiler().compile(
        task=task, agent=ClaudeReasonerAgent(), history=[], memory=[]
    )

    assert context.build_command == "npm run typecheck"
    assert context.test_command == "npm test -- --runInBand"
    assert "Never write to main." in context.constraints


def test_compile_omits_broad_test_command_for_focused_verification(tmp_path):
    (tmp_path / ".sacm.yaml").write_text(
        """
version: sacm/v1
commands:
  build: npm run typecheck
  test: npm test
""",
        encoding="utf-8",
    )
    task = make_task()
    task.target_repo_path = str(tmp_path)

    context = ContextCompiler().compile(
        task=task,
        agent=ClaudeReasonerAgent(),
        history=[],
        memory=[],
        include_test_command=False,
    )

    assert context.build_command == "npm run typecheck"
    assert context.test_command is None


def test_repository_config_rejects_multiline_command(tmp_path):
    (tmp_path / ".sacm.yaml").write_text(
        "version: sacm/v1\ncommands:\n  test: |\n    npm test\n    rm -rf /\n",
        encoding="utf-8",
    )

    with pytest.raises(RepositoryConfigError, match="single-line"):
        load_repository_config(str(tmp_path))
