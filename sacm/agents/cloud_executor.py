import subprocess

from sacm.agents.base import Agent
from sacm.core.verification_execution import (
    resource_failure_reason,
    sequential_retry_command,
)
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult

BLOCKED_COMMANDS = ["rm -rf /", "sudo", "mkfs", "shutdown", "reboot", "chmod 777 /"]


class CloudExecutorAgent(Agent):
    name = "CloudExecutor"
    role = "execution"
    CONTRIBUTES_SKILLS = ["tests_run", "commands_executed"]

    def run(self, context: AgentContext) -> AgentResult:
        commands = [
            command
            for command in (context.build_command, context.test_command)
            if command
        ] or ["echo 'No command specified'"]
        for command in commands:
            for blocked in BLOCKED_COMMANDS:
                if blocked in command:
                    return AgentResult(
                        agent_name=self.name,
                        summary=f"Blocked dangerous command: {command}",
                        actions=[],
                        artifacts=[],
                        confidence=1.0,
                        next_state_hint="blocked",
                        skills_contributed=[],
                    )

        executions = []
        retry_evidence = []
        command_outcomes = []
        try:
            for command in commands:
                original = self._execute_command(command, context.target_repo_path)
                executions.append(original)
                reason = resource_failure_reason(original)
                retry_command = sequential_retry_command(command) if reason else None
                if retry_command:
                    retry = self._execute_command(
                        retry_command, context.target_repo_path
                    )
                    executions.append(retry)
                    retry_evidence.append(
                        {
                            "reason": reason,
                            "classification": "ENVIRONMENT",
                            "category": "INFRASTRUCTURE_RESOURCE",
                            "original": original,
                            "retry": retry,
                        }
                    )
                    command_outcomes.append(retry["returncode"] == 0)
                    if retry["returncode"] != 0:
                        break
                    continue
                command_outcomes.append(original["returncode"] == 0)
                if original["returncode"] != 0:
                    break

            success = len(command_outcomes) == len(commands) and all(
                command_outcomes
            )
            last_execution = executions[-1]
            resource_failure = (
                not success and resource_failure_reason(last_execution) is not None
            )
            conf = 1.0 if success else 0.3
            return AgentResult(
                agent_name=self.name,
                summary=(
                    "Configured verification commands succeeded."
                    if success
                    else "Configured verification command failed."
                ),
                actions=[
                    *[
                        {
                            "type": "SHELL",
                            "command": execution["command"],
                            "returncode": execution["returncode"],
                        }
                        for execution in executions
                    ],
                    *[
                        {"type": "VERIFICATION_RETRY", **evidence}
                        for evidence in retry_evidence
                    ],
                    {
                        "type": "VERIFICATION",
                        "passed": success,
                        "commands": executions,
                        **(
                            {
                                "failure_classification": "ENVIRONMENT",
                                "failure_reason": "INFRASTRUCTURE_RESOURCE",
                            }
                            if resource_failure
                            else {}
                        ),
                    },
                ],
                artifacts=[
                    {
                        "type": "verification",
                        "passed": success,
                        "commands": executions,
                    },
                    *[
                        {"type": "execution_evidence", **evidence}
                        for evidence in retry_evidence
                    ],
                ],
                confidence=conf,
                next_state_hint=(
                    "reviewing"
                    if success
                    else ("blocked" if resource_failure else "debugging")
                ),
                memory_update=(
                    "Verification: "
                    + ", ".join(
                        f"{execution['command']} → "
                        f"{'OK' if execution['returncode'] == 0 else 'FAIL'}"
                        for execution in executions
                    )
                ),
                skills_contributed=[
                    {
                        "skill_name": "tests_run",
                        "evidence": f"Ran: {', '.join(commands)}",
                        "agent_name": self.name,
                        "confidence": conf,
                    },
                    {
                        "skill_name": "commands_executed",
                        "evidence": (
                            "Exit codes: "
                            + ", ".join(
                                str(execution["returncode"])
                                for execution in executions
                            )
                        ),
                        "agent_name": self.name,
                        "confidence": conf,
                    },
                ],
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                agent_name=self.name,
                summary=f"Command timed out: {command}",
                actions=[],
                artifacts=[],
                confidence=0.0,
                next_state_hint="blocked",
                skills_contributed=[],
            )

    @staticmethod
    def _execute_command(command: str, cwd: str | None) -> dict:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=cwd,
        )
        return {
            "command": command,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4_000:],
            "stderr": proc.stderr[-4_000:],
        }
