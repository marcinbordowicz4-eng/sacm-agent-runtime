from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class ArchitectAgent(Agent):
    name = "Architect"
    role = "architecture"
    CONTRIBUTES_SKILLS = ['architecture_ready', 'service_boundaries_defined']

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary="Outlined system-level approach, component boundaries, and service contracts.",
            actions=[{"type":"ARCHITECTURE","description":"Mapped services, agents, and persistence layers"}],
            artifacts=[],
            confidence=0.80,
            next_state_hint="coding",
            memory_update=f"{self.name} documented architecture for {context.task_id}",
            skills_contributed=[
                {"skill_name":"architecture_ready","evidence":"Designed module structure and boundaries","agent_name":self.name,"confidence":0.80},
                {"skill_name":"service_boundaries_defined","evidence":"Defined service contracts","agent_name":self.name,"confidence":0.80},
            ],
        )
