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
        command = context.test_command or "echo 'No command specified'"
        for blocked in BLOCKED_COMMANDS:
            if blocked in command:
                return AgentResult(
                    agent_name=self.name,
                    summary=f"Blocked dangerous command: {command}",
                    actions=[], artifacts=[], confidence=1.0,
                    next_state_hint="blocked", skills_contributed=[],
                )
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=600, cwd=context.target_repo_path,
            )
            success = proc.returncode == 0
            conf = 1.0 if success else 0.3
            return AgentResult(
                agent_name=self.name,
                summary=f"Command {'succeeded' if success else 'failed'}: {command}",
                actions=[{"type": "SHELL", "command": command, "returncode": proc.returncode}],
                artifacts=[{"type": "log", "content": (proc.stdout + proc.stderr)[:4000]}],
                confidence=conf,
                next_state_hint="testing" if success else "debugging",
                memory_update=f"Command: {command} → {'OK' if success else 'FAIL'}",
                skills_contributed=[
                    {"skill_name":"tests_run","evidence":f"Ran: {command}","agent_name":self.name,"confidence":conf},
                    {"skill_name":"commands_executed","evidence":f"Exit: {proc.returncode}","agent_name":self.name,"confidence":conf},
                ],
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                agent_name=self.name, summary=f"Command timed out: {command}",
                actions=[], artifacts=[], confidence=0.0,
                next_state_hint="blocked", skills_contributed=[],
            )
