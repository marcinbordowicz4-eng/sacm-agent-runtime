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
            summary=(
                "Code implementation requires the isolated CodexExecutor; "
                "no source changes were made by the routing agent."
            ),
            actions=[],
            artifacts=[],
            confidence=0.0,
            next_state_hint="coding",
            memory_update=(
                f"{self.name} routed implementation to CodexExecutor for: "
                f"{context.task[:100]}"
            ),
            skills_contributed=[],
        )
