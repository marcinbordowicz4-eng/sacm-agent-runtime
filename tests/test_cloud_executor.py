import sys
from types import SimpleNamespace

from sacm.agents.cloud_executor import CloudExecutorAgent
from sacm.core.verification_execution import (
    resource_failure_reason,
    sequential_retry_command,
)
from sacm.core.verifier import Verifier
from sacm.schemas.context import AgentContext


def _command(exit_code: int) -> str:
    return f'{sys.executable} -c "raise SystemExit({exit_code})"'


def test_cloud_executor_completes_after_build_and_tests_pass(tmp_path):
    context = AgentContext(
        task_id="task-1",
        task="Verify implementation",
        goal="Verify implementation",
        current_state="testing",
        target_repo_path=str(tmp_path),
        build_command=_command(0),
        test_command=_command(0),
    )

    result = CloudExecutorAgent().run(context)

    assert result.next_state_hint == "reviewing"
    assert result.confidence == 1.0
    assert [action["type"] for action in result.actions] == [
        "SHELL",
        "SHELL",
        "VERIFICATION",
    ]
    assert result.actions[-1]["passed"] is True
    assert Verifier.has_successful_verification(result) is True


def test_cloud_executor_stops_after_failed_build(tmp_path):
    context = AgentContext(
        task_id="task-1",
        task="Verify implementation",
        goal="Verify implementation",
        current_state="testing",
        target_repo_path=str(tmp_path),
        build_command=_command(1),
        test_command=_command(0),
    )

    result = CloudExecutorAgent().run(context)

    assert result.next_state_hint == "debugging"
    assert [action["type"] for action in result.actions] == [
        "SHELL",
        "VERIFICATION",
    ]
    assert result.actions[-1]["passed"] is False
    assert Verifier.has_successful_verification(result) is False


def test_cloud_executor_retries_jest_resource_failure_sequentially(
    tmp_path, monkeypatch
):
    calls = []
    results = iter(
        [
            {
                "command": "npm test",
                "returncode": 137,
                "stdout": "",
                "stderr": "A jest worker process was terminated by SIGKILL",
            },
            {
                "command": "npm test -- --runInBand",
                "returncode": 0,
                "stdout": "passed",
                "stderr": "",
            },
        ]
    )

    def fake_execute(command, cwd):
        calls.append(command)
        return next(results)

    monkeypatch.setattr(
        CloudExecutorAgent, "_execute_command", staticmethod(fake_execute)
    )
    context = AgentContext(
        task_id="task-1",
        task="Verify implementation",
        goal="Verify implementation",
        current_state="testing",
        target_repo_path=str(tmp_path),
        test_command="npm test",
    )

    result = CloudExecutorAgent().run(context)

    assert calls == ["npm test", "npm test -- --runInBand"]
    assert result.next_state_hint == "reviewing"
    retry = next(action for action in result.actions if action["type"] == "VERIFICATION_RETRY")
    assert retry["classification"] == "ENVIRONMENT"
    assert retry["category"] == "INFRASTRUCTURE_RESOURCE"
    assert retry["original"]["returncode"] == 137
    assert retry["retry"]["returncode"] == 0


def test_cloud_executor_does_not_retry_assertion_failure(tmp_path, monkeypatch):
    calls = []

    def fake_execute(command, cwd):
        calls.append(command)
        return {
            "command": command,
            "returncode": 1,
            "stdout": "Expected true to be false",
            "stderr": "",
        }

    monkeypatch.setattr(
        CloudExecutorAgent, "_execute_command", staticmethod(fake_execute)
    )
    context = AgentContext(
        task_id="task-1",
        task="Verify implementation",
        goal="Verify implementation",
        current_state="testing",
        target_repo_path=str(tmp_path),
        test_command="npm test",
    )

    result = CloudExecutorAgent().run(context)

    assert calls == ["npm test"]
    assert result.next_state_hint == "debugging"
    assert not any(action["type"] == "VERIFICATION_RETRY" for action in result.actions)


def test_cloud_executor_reports_repeated_oom_as_environment_failure(
    tmp_path, monkeypatch
):
    def fake_execute(command, cwd):
        return {
            "command": command,
            "returncode": 137,
            "stdout": "",
            "stderr": "JavaScript heap out of memory",
        }

    monkeypatch.setattr(
        CloudExecutorAgent, "_execute_command", staticmethod(fake_execute)
    )
    context = AgentContext(
        task_id="task-1",
        task="Verify implementation",
        goal="Verify implementation",
        current_state="testing",
        target_repo_path=str(tmp_path),
        test_command="npm test",
    )

    result = CloudExecutorAgent().run(context)

    assert result.next_state_hint == "blocked"
    assert result.actions[-1]["failure_classification"] == "ENVIRONMENT"
    assert result.actions[-1]["failure_reason"] == "INFRASTRUCTURE_RESOURCE"


def test_resource_failure_classifies_sigkill_and_oom():
    assert resource_failure_reason(
        {"returncode": -9, "stdout": "", "stderr": ""}
    )
    assert resource_failure_reason(
        SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="JavaScript heap out of memory",
        ).__dict__
    )
    assert sequential_retry_command("npm test -- --runInBand") is None
