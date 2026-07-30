from sacm.agents.base import Agent
from sacm.core.repository_config import load_repository_config
from sacm.infrastructure.db.models import ContextEvent, MemoryChunk, Task
from sacm.schemas.context import AgentContext
from sacm.schemas.contracts import AgentTaskV1


class ContextCompiler:
    def __init__(self, token_budget: int = 12000):
        self.token_budget = token_budget

    def compile(
        self,
        task: Task,
        agent: Agent,
        history: list[ContextEvent],
        memory: list[MemoryChunk],
    ) -> AgentContext:
        repository_config = load_repository_config(task.target_repo_path)
        task_text = self._trim(task.description, self.token_budget)
        goal = self._trim(f"Complete task: {task.title}", self.token_budget)
        constraints = [
            f"Agent role: {agent.role}",
            f"Token budget: {self.token_budget}",
            *(repository_config.constraints if repository_config else []),
        ]
        remaining = self.token_budget - self._estimate_tokens(task_text)
        remaining -= self._estimate_tokens(goal)
        remaining -= sum(self._estimate_tokens(item) for item in constraints)

        relevant_memory, remaining = self._fit(
            [chunk.content for chunk in memory[:8]], remaining
        )
        previous_findings, _ = self._fit(
            [
            event.payload.get("summary", "")
            for event in history[:5]
            if event.event_type == "agent_result"
            ],
            remaining,
        )
        return AgentContext(
            task_id=task.id,
            task=task_text,
            goal=goal,
            current_state=task.status,
            target_repo_path=task.target_repo_path,
            relevant_memory=relevant_memory,
            files={},
            constraints=constraints,
            previous_findings=previous_findings,
            test_command=(
                repository_config.commands.test if repository_config else None
            ),
            build_command=(
                repository_config.commands.build if repository_config else None
            ),
            token_budget=self.token_budget,
        )

    def compile_v1(
        self,
        *,
        run_id: str,
        step_id: str,
        agent: Agent,
        context: AgentContext,
    ) -> AgentTaskV1:
        return AgentTaskV1(
            run_id=run_id,
            step_id=step_id,
            role=agent.contract_role,
            objective=context.goal,
            acceptance_criteria=context.constraints,
            context_references=context.relevant_memory + context.previous_findings,
            token_budget=context.token_budget,
            timeout_seconds=300,
            execution_context=context.model_dump(),
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    @staticmethod
    def _trim(text: str, budget: int) -> str:
        return text[: max(0, budget) * 4]

    def _fit(self, values: list[str], remaining: int) -> tuple[list[str], int]:
        fitted: list[str] = []
        for value in values:
            if not value or remaining <= 0:
                continue
            trimmed = self._trim(value, remaining)
            fitted.append(trimmed)
            remaining -= self._estimate_tokens(trimmed)
        return fitted, remaining
