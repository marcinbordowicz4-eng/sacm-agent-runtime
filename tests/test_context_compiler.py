from unittest.mock import MagicMock

from sacm.agents.claude_reasoner import ClaudeReasonerAgent
from sacm.core.context_compiler import ContextCompiler


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
