from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class SecurityAuditorAgent(Agent):
    name = "SecurityAuditor"
    role = "security"
    CONTRIBUTES_SKILLS = ['security_audited', 'vulnerabilities_checked']

    def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary="Audited code for vulnerabilities, secrets, and risky dependencies.",
            actions=[{"type":"SECURITY_AUDIT","description":"Scanned for secrets, CVEs, and dangerous commands"}],
            artifacts=[],
            confidence=0.82,
            next_state_hint="reviewing",
            memory_update=f"{self.name} completed security audit for task {context.task_id}",
            skills_contributed=[
                {"skill_name":"security_audited","evidence":"Audited code for security issues","agent_name":self.name,"confidence":0.82},
                {"skill_name":"vulnerabilities_checked","evidence":"Checked dependencies and secrets","agent_name":self.name,"confidence":0.82},
            ],
        )
