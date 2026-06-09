from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class ArchitectAgent(Agent):
    name = "Architect"
    role = "architecture"

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary="Outlined the system-level approach and component boundaries.",
            actions=[
                {
                    "type": "ARCHITECTURE",
                    "description": "Mapped services, agents, and persistence layers",
                }
            ],
            artifacts=[],
            confidence=0.8,
            next_state_hint="coding",
            memory_update=f"{self.name} documented architecture for {context.task_id}",
        )
