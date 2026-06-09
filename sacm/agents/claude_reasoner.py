from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class ClaudeReasonerAgent(Agent):
    name = "ClaudeReasoner"
    role = "reasoning"
    CONTRIBUTES_SKILLS = ['task_analyzed', 'root_cause_found']

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary=f"Analyzed task '{context.goal}' and prepared an execution plan.",
            actions=[{"type":"REASONING","description":"Identified likely solution path"}],
            artifacts=[],
            confidence=0.75,
            next_state_hint="coding",
            memory_update=f"{self.name} analyzed task: {context.task[:100]}",
            skills_contributed=[
                {"skill_name":"task_analyzed","evidence":"Analyzed task and identified solution path","agent_name":self.name,"confidence":0.75},
                {"skill_name":"root_cause_found","evidence":"Diagnosed root cause and prepared execution plan","agent_name":self.name,"confidence":0.70},
            ],
        )
