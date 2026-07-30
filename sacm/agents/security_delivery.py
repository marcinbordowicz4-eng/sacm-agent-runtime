from pathlib import Path

from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class SecurityDeliveryAgent(Agent):
    """Verifies that repository-native security checks are configured in CI."""

    name = "SecurityDelivery"
    role = "security-delivery"
    CONTRIBUTES_SKILLS = ["security_ci_preflighted"]

    def run(self, context: AgentContext) -> AgentResult:
        if not context.target_repo_path:
            return AgentResult(
                agent_name=self.name,
                summary="Security CI preflight requires a target repository path.",
                confidence=1.0,
                next_state_hint="blocked",
            )

        workflow_dir = Path(context.target_repo_path) / ".github/workflows"
        codeql = (workflow_dir / "codeql.yml").exists()
        dependency_review = (workflow_dir / "dependency-review.yml").exists()
        ready = codeql and dependency_review
        missing = [
            name
            for name, present in (
                ("CodeQL workflow", codeql),
                ("dependency review workflow", dependency_review),
            )
            if not present
        ]
        summary = (
            "CodeQL and dependency review workflows are configured."
            if ready
            else f"Security CI configuration is incomplete: {', '.join(missing)}."
        )
        return AgentResult(
            agent_name=self.name,
            summary=summary,
            actions=[
                {
                    "type": "SECURITY_CI_PREFLIGHT",
                    "codeql_configured": codeql,
                    "dependency_review_configured": dependency_review,
                }
            ],
            confidence=1.0 if ready else 0.3,
            next_state_hint="reviewing" if ready else "planning",
            memory_update=summary,
            skills_contributed=[
                {
                    "skill_name": "security_ci_preflighted",
                    "evidence": summary,
                    "agent_name": self.name,
                    "confidence": 1.0 if ready else 0.3,
                }
            ],
        )
