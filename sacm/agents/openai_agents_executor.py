from sacm.adapters.openai_agents_adapter import OpenAIAgentsAdapter
from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class OpenAIAgentsExecutorAgent(Agent):
    """Runs a privacy-preserving OpenAI Agents SDK reasoning turn."""

    name = "OpenAIAgentsExecutor"
    role = "agent-sdk-reasoning"
    CONTRIBUTES_SKILLS = ["agent_sdk_reasoning_completed"]

    def run(self, context: AgentContext) -> AgentResult:
        result = OpenAIAgentsAdapter().run(context)
        if not result["available"]:
            return AgentResult(
                agent_name=self.name,
                summary=result["reason"],
                confidence=0.0,
                next_state_hint=context.current_state,
            )

        summary = result["output"]
        return AgentResult(
            agent_name=self.name,
            summary=summary,
            actions=[{"type": "OPENAI_AGENTS_EXECUTION"}],
            artifacts=[result["usage"]],
            confidence=0.7,
            next_state_hint="coding",
            memory_update=summary[:500],
            skills_contributed=[
                {
                    "skill_name": "agent_sdk_reasoning_completed",
                    "evidence": "OpenAI Agents SDK returned a reasoning result.",
                    "agent_name": self.name,
                    "confidence": 0.7,
                }
            ],
        )
