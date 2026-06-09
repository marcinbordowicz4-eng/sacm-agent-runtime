from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class ClaudeReasonerAgent(Agent):
    name = "ClaudeReasoner"
    role = "reasoning"

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary=f"Analyzed task '{context.goal}' and prepared an execution plan.",
            actions=[{"type": "REASONING", "description": "Identified likely solution path"}],
            artifacts=[],
            confidence=0.75,
            next_state_hint="coding",
            memory_update=f"{self.name} analyzed task: {context.task[:100]}",
        )
