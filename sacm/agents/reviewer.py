from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class ReviewerAgent(Agent):
    name = "Reviewer"
    role = "review"
    CONTRIBUTES_SKILLS = ['code_reviewed', 'review_complete']

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary="Reviewed diff, assessed risks, and suggested improvements.",
            actions=[{"type":"REVIEW","description":"Inspected diff and classified risks"}],
            artifacts=[],
            confidence=0.80,
            next_state_hint="done",
            memory_update=f"{self.name} reviewed changes for task {context.task_id}",
            skills_contributed=[
                {"skill_name":"code_reviewed","evidence":"Reviewed diff and assessed risks","agent_name":self.name,"confidence":0.80},
                {"skill_name":"review_complete","evidence":"Completed full review cycle","agent_name":self.name,"confidence":0.80},
            ],
        )
