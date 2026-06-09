from sacm.schemas.context import AgentContext


class ClaudeAdapter:
    def build_prompt(self, context: AgentContext) -> str:
        return f"Claude prompt for {context.goal}"
