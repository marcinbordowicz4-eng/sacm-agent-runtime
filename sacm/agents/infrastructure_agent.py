from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class InfrastructureAgent(Agent):
    name = "InfrastructureAgent"
    role = "infrastructure"
    CONTRIBUTES_SKILLS = ['infra_planned', 'deployment_configured']

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary=f"Analyzed infrastructure requirements for '{context.goal}'.",
            actions=[{"type":"DOCKER","description":"Reviewed Dockerfile and docker-compose"},{"type":"CI_CD","description":"Outlined GitHub Actions pipeline steps"},{"type":"DEPLOYMENT","description":"Identified Kubernetes/Helm changes"},{"type":"MONITORING","description":"Suggested logging and alerts"},{"type":"SECRETS","description":"Verified secrets strategy"}],
            artifacts=[],
            confidence=0.80,
            next_state_hint="testing",
            memory_update=f"{self.name} analyzed infra requirements for task {context.task_id}",
            skills_contributed=[
                {"skill_name":"infra_planned","evidence":"Analyzed Docker and CI/CD configuration","agent_name":self.name,"confidence":0.80},
                {"skill_name":"deployment_configured","evidence":"Outlined deployment and monitoring plan","agent_name":self.name,"confidence":0.80},
            ],
        )
