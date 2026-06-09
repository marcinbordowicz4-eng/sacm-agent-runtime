from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class FrontendAgent(Agent):
    name = "FrontendAgent"
    role = "frontend"
    CONTRIBUTES_SKILLS = ['ui_designed', 'components_planned']

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary=f"Designed frontend solution for '{context.goal}'.",
            actions=[{"type":"COMPONENT_DESIGN","description":"Outlined React/Vue components and props"},{"type":"STATE_MANAGEMENT","description":"Mapped state shape and selectors"},{"type":"STYLING","description":"Defined CSS/Tailwind changes"},{"type":"ACCESSIBILITY","description":"Identified ARIA roles and contrast issues"}],
            artifacts=[],
            confidence=0.76,
            next_state_hint="coding",
            memory_update=f"{self.name} analyzed UI requirements for task {context.task_id}",
            skills_contributed=[
                {"skill_name":"ui_designed","evidence":"Designed UI components and state","agent_name":self.name,"confidence":0.76},
                {"skill_name":"components_planned","evidence":"Outlined React/Vue component structure","agent_name":self.name,"confidence":0.76},
            ],
        )
