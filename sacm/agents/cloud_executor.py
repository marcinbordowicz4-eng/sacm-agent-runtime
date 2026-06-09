import subprocess

from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult

BLOCKED_COMMANDS = ["rm -rf /", "sudo", "mkfs", "shutdown", "reboot", "chmod 777 /"]


class CloudExecutorAgent(Agent):
    name = "CloudExecutor"
    role = "execution"

    def run(self, context: AgentContext) -> AgentResult:
        command = context.test_command or "echo 'No command specified'"
        for blocked in BLOCKED_COMMANDS:
            if blocked in command:
                return AgentResult(
                    agent_name=self.name,
                    summary=f"Blocked dangerous command: {command}",
                    actions=[],
                    artifacts=[],
                    confidence=1.0,
                    next_state_hint="blocked",
                )
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=context.target_repo_path,
            )
            success = result.returncode == 0
            output = (result.stdout + result.stderr)[:4000]
            return AgentResult(
                agent_name=self.name,
                summary=f"Command {'succeeded' if success else 'failed'}: {command}",
                actions=[
                    {
                        "type": "SHELL",
                        "command": command,
                        "returncode": result.returncode,
                    }
                ],
                artifacts=[{"type": "log", "content": output}],
                confidence=1.0 if success else 0.3,
                next_state_hint="testing" if success else "debugging",
                memory_update=f"Command: {command} → {'OK' if success else 'FAIL'}",
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                agent_name=self.name,
                summary=f"Command timed out: {command}",
                actions=[],
                artifacts=[],
                confidence=0.0,
                next_state_hint="blocked",
            )
