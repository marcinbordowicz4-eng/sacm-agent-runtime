import os

from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class OpenTelemetryCostAgent(Agent):
    """Checks whether cost telemetry is sufficient for a task-level review."""

    name = "OpenTelemetryCost"
    role = "cost-observability"
    CONTRIBUTES_SKILLS = ["cost_telemetry_assessed"]

    def run(self, context: AgentContext) -> AgentResult:
        otel_enabled = os.getenv("SACM_OTEL_ENABLED", "false").lower() == "true"
        has_endpoint = bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))
        has_pricing = bool(
            os.getenv("SACM_OPENAI_EMBEDDING_INPUT_COST_PER_MILLION_USD")
        )
        ready = otel_enabled and has_endpoint and has_pricing
        missing = [
            name
            for name, present in (
                ("SACM_OTEL_ENABLED=true", otel_enabled),
                ("OTEL_EXPORTER_OTLP_ENDPOINT", has_endpoint),
                ("SACM_OPENAI_EMBEDDING_INPUT_COST_PER_MILLION_USD", has_pricing),
            )
            if not present
        ]
        summary = (
            "Cost telemetry is configured for OpenAI embedding usage."
            if ready
            else f"Cost telemetry cannot estimate costs until {', '.join(missing)} is configured."
        )
        return AgentResult(
            agent_name=self.name,
            summary=summary,
            actions=[
                {
                    "type": "COST_TELEMETRY",
                    "otel_enabled": otel_enabled,
                    "collector_configured": has_endpoint,
                    "pricing_configured": has_pricing,
                }
            ],
            confidence=1.0,
            next_state_hint=context.current_state,
            memory_update=summary,
            skills_contributed=[
                {
                    "skill_name": "cost_telemetry_assessed",
                    "evidence": summary,
                    "agent_name": self.name,
                    "confidence": 1.0,
                }
            ],
        )
