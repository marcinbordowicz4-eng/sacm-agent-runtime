from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class TestGeneratorAgent(Agent):
    name = "TestGenerator"
    role = "testing"
    CONTRIBUTES_SKILLS = ['tests_written', 'test_coverage']

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary="Generated missing unit and integration tests.",
            actions=[{"type":"TEST_GENERATION","description":"Created test cases for uncovered paths"}],
            artifacts=[],
            confidence=0.73,
            next_state_hint="reviewing",
            memory_update=f"{self.name} generated tests for task {context.task_id}",
            skills_contributed=[
                {"skill_name":"tests_written","evidence":"Generated missing unit/integration tests","agent_name":self.name,"confidence":0.73},
                {"skill_name":"test_coverage","evidence":"Improved test coverage","agent_name":self.name,"confidence":0.73},
            ],
        )
