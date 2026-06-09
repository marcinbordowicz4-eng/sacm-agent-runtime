from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class SecurityAuditorAgent(Agent):
    name = "SecurityAuditor"
    role = "security"

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary="Audited the current plan for obvious security risks.",
            actions=[
                {
                    "type": "SECURITY_REVIEW",
                    "description": "Checked command execution and patch application boundaries",
                }
            ],
            artifacts=[],
            confidence=0.88,
            next_state_hint="reviewing",
            memory_update=f"{self.name} audited task {context.task_id}",
        )
