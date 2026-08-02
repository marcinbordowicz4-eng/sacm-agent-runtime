import subprocess

from sacm.agents.base import Agent
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
        try:
            for command in commands:
                proc = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    cwd=context.target_repo_path,
                )
                executions.append(
                    {
                        "command": command,
                        "returncode": proc.returncode,
                        "stdout": proc.stdout[-4_000:],
                        "stderr": proc.stderr[-4_000:],
                    }
                )
                if proc.returncode != 0:
                    break

            success = len(executions) == len(commands) and all(
                execution["returncode"] == 0 for execution in executions
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
                    {
                        "type": "VERIFICATION",
                        "passed": success,
                        "commands": executions,
                    },
                ],
                artifacts=[
                    {
                        "type": "verification",
                        "passed": success,
                        "commands": executions,
                    }
                ],
                confidence=conf,
                next_state_hint="reviewing" if success else "debugging",
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
