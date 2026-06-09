from sacm.schemas.context import AgentContext


class CodexAdapter:
    def build_prompt(self, context: AgentContext) -> str:
        return f"Codex prompt for {context.goal}"
