import os
from typing import Any

from sacm.core.observability import ObservabilityService
from sacm.schemas.context import AgentContext


class OpenAIAgentsAdapter:
    """Optional OpenAI Agents SDK executor with privacy-preserving tracing."""

    def run(self, context: AgentContext) -> dict[str, Any]:
        if os.getenv("SACM_OPENAI_AGENTS_ENABLED", "false").lower() != "true":
            return {"available": False, "reason": "OpenAI Agents SDK is disabled."}
        if not os.getenv("OPENAI_API_KEY"):
            return {"available": False, "reason": "OPENAI_API_KEY is not configured."}

        try:
            from agents import Agent, RunConfig, Runner, trace
        except ImportError:
            return {
                "available": False,
                "reason": "Install the openai-agents optional dependency.",
            }

        model = os.getenv("SACM_OPENAI_AGENTS_MODEL") or None
        agent = Agent(
            name="SACM OpenAI Executor",
            instructions=(
                "Analyze the supplied software-engineering goal and return a concise, "
                "actionable plan. Do not include secrets or personally identifiable data."
            ),
            model=model,
        )
        run_config = RunConfig(trace_include_sensitive_data=False)
        with trace("sacm.openai_agents", group_id=context.task_id):
            result = Runner.run_sync(agent, context.goal, run_config=run_config)

        usage = result.context_wrapper.usage
        input_tokens = int(usage.input_tokens)
        output_tokens = int(usage.output_tokens)
        estimated_cost = ObservabilityService().record_model_usage(
            provider="openai_agents",
            model=model or "provider-default",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            operation="agent_reasoning",
        )
        return {
            "available": True,
            "output": str(result.final_output),
            "usage": {
                "type": "usage",
                "provider": "openai_agents",
                "model": model or "provider-default",
                "operation": "agent_reasoning",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": estimated_cost,
            },
        }
