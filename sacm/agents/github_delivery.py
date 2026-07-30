from sacm.adapters.github_adapter import GitHubAdapter
from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class GitHubDeliveryAgent(Agent):
    """Preflights GitHub CLI delivery before a branch is pushed or reviewed."""

    name = "GitHubDelivery"
    role = "delivery"
    CONTRIBUTES_SKILLS = ["github_delivery_preflighted"]

    def run(self, context: AgentContext) -> AgentResult:
        if not context.target_repo_path:
            return AgentResult(
                agent_name=self.name,
                summary="GitHub delivery requires a target repository path.",
                confidence=1.0,
                next_state_hint="blocked",
            )

        status = GitHubAdapter(context.target_repo_path).auth_status()
        authenticated = status["returncode"] == 0
        summary = (
            "GitHub CLI authentication is ready for issue and pull-request delivery."
            if authenticated
            else f"GitHub CLI preflight failed: {status['stderr'] or status['stdout']}"
        )
        return AgentResult(
            agent_name=self.name,
            summary=summary,
            actions=[
                {
                    "type": "GITHUB_DELIVERY_PREFLIGHT",
                    "authenticated": authenticated,
                }
            ],
            artifacts=[
                {
                    "type": "github_auth_log",
                    "content": (status["stdout"] + status["stderr"])[:8_000],
                }
            ],
            confidence=1.0 if authenticated else 0.0,
            next_state_hint="reviewing" if authenticated else "blocked",
            memory_update=summary,
            skills_contributed=[
                {
                    "skill_name": "github_delivery_preflighted",
                    "evidence": summary,
                    "agent_name": self.name,
                    "confidence": 1.0 if authenticated else 0.0,
                }
            ],
        )
