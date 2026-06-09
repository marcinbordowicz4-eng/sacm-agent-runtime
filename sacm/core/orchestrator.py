import os

from sqlalchemy.orm import Session

from sacm.core.agent_registry import AgentRegistry
from sacm.core.context_compiler import ContextCompiler
from sacm.core.embedding_service import EmbeddingService
from sacm.core.event_service import EventService
from sacm.core.feedback_service import FeedbackService
from sacm.core.memory_service import MemoryService
from sacm.core.router import RouterService
from sacm.core.state_service import StateService
from sacm.core.task_service import TaskService
from sacm.core.verifier import Verifier

MAX_STEPS = int(os.getenv("SACM_MAX_AGENT_STEPS", "10"))


class Orchestrator:
    def __init__(self, db: Session):
        self.db = db
        self.task_service = TaskService(db)
        self.event_service = EventService(db)
        self.memory_service = MemoryService(db)
        self.state_service = StateService(db)
        self.agent_registry = AgentRegistry()
        self.context_compiler = ContextCompiler()
        self.router_service = RouterService()
        self.verifier = Verifier()
        self.embedding_service = EmbeddingService()
        self.feedback_service = FeedbackService(db, self.router_service)

    def run_task(self, task_id: str, max_steps: int = MAX_STEPS) -> dict:
        task = self.task_service.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        self.task_service.update_status(task_id, "planning")
        steps_taken = 0

        for step_index in range(max_steps):
            steps_taken = step_index + 1
            task = self.task_service.get(task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found during execution")

            history = self.event_service.get_recent_events(task_id)
            memory = self.memory_service.search(task_id, task.description, top_k=8)

            context_vector = self.embedding_service.embed_task_context(task, history, memory)
            belief_state = self.state_service.get_belief_state(task_id)

            routing_result = self.router_service.route(
                context_vector=context_vector,
                belief_state=belief_state,
            )
            selected_agent = self.agent_registry.get_by_index(
                routing_result["selected_agent_index"]
            )

            compiled_context = self.context_compiler.compile(
                task=task,
                agent=selected_agent,
                history=history,
                memory=memory,
            )
            result = selected_agent.run(compiled_context)

            self.event_service.save_agent_result(task_id, selected_agent.name, result)
            self.memory_service.add_from_agent_result(task_id, result)
            self.state_service.update_belief_state(task_id, routing_result["next_belief"])

            done = self.verifier.is_done(task, result)

            # --- feedback loop: update router weights and agent quality score ---
            self.feedback_service.record(
                context_vector=context_vector,
                belief_state=belief_state,
                selected_agent_index=routing_result["selected_agent_index"],
                agent_name=selected_agent.name,
                result=result,
                task_done=done,
            )

            if done:
                self.task_service.mark_done(task_id)
                break

            if result.next_state_hint:
                self.task_service.update_status(task_id, result.next_state_hint)

        final_task = self.task_service.get(task_id)
        if not final_task:
            raise ValueError(f"Task {task_id} missing after execution")
        events = self.event_service.get_recent_events(task_id, limit=5)
        return {
            "task_id": task_id,
            "status": final_task.status,
            "steps": steps_taken,
            "last_events": [event.payload for event in events],
        }
