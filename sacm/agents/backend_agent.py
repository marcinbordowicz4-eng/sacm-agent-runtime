from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class BackendAgent(Agent):
    name = "BackendAgent"
    role = "backend"
    CONTRIBUTES_SKILLS = ['api_designed', 'service_layer_ready']

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary=f"Designed backend solution for '{context.goal}'.",
            actions=[{"type":"API_DESIGN","description":"Defined REST endpoints and schemas"},{"type":"SERVICE_LAYER","description":"Outlined business logic boundaries"},{"type":"DB_MIGRATION","description":"Identified schema migration steps"}],
            artifacts=[],
            confidence=0.78,
            next_state_hint="coding",
            memory_update=f"{self.name} analyzed backend requirements for task {context.task_id}",
            skills_contributed=[
                {"skill_name":"api_designed","evidence":"Defined REST endpoints and request/response schemas","agent_name":self.name,"confidence":0.78},
                {"skill_name":"service_layer_ready","evidence":"Outlined service and repository layer","agent_name":self.name,"confidence":0.78},
            ],
        )
