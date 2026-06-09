from sacm.agents.base import Agent
from sacm.infrastructure.db.models import ContextEvent, MemoryChunk, Task
from sacm.schemas.context import AgentContext


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
        relevant_memory = [chunk.content for chunk in memory[:8]]
        previous_findings = [
            event.payload.get("summary", "")
            for event in history[:5]
            if event.event_type == "agent_result"
        ]
        constraints = [
            f"Agent role: {agent.role}",
            f"Token budget: {self.token_budget}",
        ]
        return AgentContext(
            task_id=task.id,
            task=task.description,
            goal=f"Complete task: {task.title}",
            current_state=task.status,
            target_repo_path=task.target_repo_path,
            relevant_memory=relevant_memory,
            files={},
            constraints=constraints,
            previous_findings=[finding for finding in previous_findings if finding],
        )
