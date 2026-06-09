from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class TestGeneratorAgent(Agent):
    name = "TestGenerator"
    role = "testing"

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary="Identified missing regression coverage and proposed deterministic tests.",
            actions=[
                {
                    "type": "TEST_FILE",
                    "description": "Suggested adding regression coverage for the task",
                }
            ],
            artifacts=[],
            confidence=0.84,
            next_state_hint="reviewing",
            memory_update=f"{self.name} suggested test coverage for {context.task_id}",
        )
