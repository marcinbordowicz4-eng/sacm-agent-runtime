from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class CodexCoderAgent(Agent):
    name = "CodexCoder"
    role = "coding"

    def run(self, context: AgentContext) -> AgentResult:
        target = context.target_repo_path or "current workspace"
        return AgentResult(
            agent_name=self.name,
            summary=f"Prepared a minimal code change strategy for {target}.",
            actions=[
                {
                    "type": "CODE_DIFF",
                    "description": "Drafted a focused implementation plan",
                }
            ],
            artifacts=[],
            confidence=0.82,
            next_state_hint="testing",
            memory_update=f"{self.name} proposed code changes for {target}",
        )
