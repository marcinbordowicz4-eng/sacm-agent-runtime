import sys

from sacm.agents.cloud_executor import CloudExecutorAgent
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
