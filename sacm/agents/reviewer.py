from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class ReviewerAgent(Agent):
    name = "Reviewer"
    role = "review"

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary="Reviewed the proposed changes for correctness and regression risk.",
            actions=[
                {
                    "type": "REVIEW",
                    "risk": "low",
                    "description": "No critical issues identified in stub review",
                }
            ],
            artifacts=[],
            confidence=0.9,
            next_state_hint="done",
            memory_update=f"{self.name} reviewed task state {context.current_state}",
        )
