from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class CodexCoderAgent(Agent):
    name = "CodexCoder"
    role = "coding"
    CONTRIBUTES_SKILLS = ['code_implemented', 'patch_created']

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary=f"Generated code changes for '{context.goal}'.",
            actions=[{"type":"CODE_EDIT","description":"Applied minimal code changes"}],
            artifacts=[],
            confidence=0.78,
            next_state_hint="testing",
            memory_update=f"{self.name} implemented changes for: {context.task[:100]}",
            skills_contributed=[
                {"skill_name":"code_implemented","evidence":"Generated and applied code changes","agent_name":self.name,"confidence":0.78},
                {"skill_name":"patch_created","evidence":"Created minimal diff / patch","agent_name":self.name,"confidence":0.73},
            ],
        )
