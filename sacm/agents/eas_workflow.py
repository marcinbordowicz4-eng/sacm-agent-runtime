from pathlib import Path

from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class EASWorkflowAgent(Agent):
    """Checks that a target React Native repository can build through EAS."""

    name = "EASWorkflow"
    role = "mobile-release"
    CONTRIBUTES_SKILLS = ["eas_release_preflighted"]

    def run(self, context: AgentContext) -> AgentResult:
        if not context.target_repo_path:
            return AgentResult(
                agent_name=self.name,
                summary="EAS release preflight requires a target repository path.",
                confidence=1.0,
                next_state_hint="blocked",
            )

        root = Path(context.target_repo_path)
        expo_configured = any(
            (root / filename).exists()
            for filename in ("app.json", "app.config.js", "app.config.ts")
        )
        eas_configured = (root / "eas.json").exists()
        preview_workflow = (root / ".github/workflows/eas-preview.yml").exists()
        ready = expo_configured and eas_configured and preview_workflow
        missing = [
            name
            for name, present in (
                ("Expo app configuration", expo_configured),
                ("eas.json", eas_configured),
                (".github/workflows/eas-preview.yml", preview_workflow),
            )
            if not present
        ]
        summary = (
            "EAS preview-build workflow is configured."
            if ready
            else f"EAS preview-build workflow is missing: {', '.join(missing)}."
        )
        return AgentResult(
            agent_name=self.name,
            summary=summary,
            actions=[
                {
                    "type": "EAS_PREFLIGHT",
                    "expo_configured": expo_configured,
                    "eas_configured": eas_configured,
                    "preview_workflow_configured": preview_workflow,
                }
            ],
            confidence=1.0 if ready else 0.3,
            next_state_hint="testing" if ready else "planning",
            memory_update=summary,
            skills_contributed=[
                {
                    "skill_name": "eas_release_preflighted",
                    "evidence": summary,
                    "agent_name": self.name,
                    "confidence": 1.0 if ready else 0.3,
                }
            ],
        )
