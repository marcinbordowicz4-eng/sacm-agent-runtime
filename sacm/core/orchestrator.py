import os

from sqlalchemy.orm import Session

from sacm.core.agent_registry import AgentRegistry
from sacm.core.context_compiler import ContextCompiler
from sacm.core.embedding_service import EmbeddingService
from sacm.core.event_service import EventService
from sacm.core.feedback_service import FeedbackService
from sacm.core.memory_service import MemoryService
from sacm.core.observability import ObservabilityService
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
        self.observability = ObservabilityService()

    def run_task(
        self, task_id: str, max_steps: int = MAX_STEPS, run_id: str | None = None
    ) -> dict:
        task = self.task_service.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        self.task_service.update_status(task_id, "planning")
        steps_taken = 0
        trace = self.observability.start_task(task_id, max_steps)

        for step_index in range(max_steps):
            steps_taken = step_index + 1
            task = self.task_service.get(task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found during execution")

            history = self.event_service.get_recent_events(task_id)
            memory = self.memory_service.search(task_id, task.description, top_k=8)
            trace.record(
                "sacm.retrieve_memory",
                "retriever",
                {"task_id": task_id, "query_length": len(task.description), "top_k": 8},
                {
                    "chunk_ids": [chunk.id for chunk in memory],
                    "result_count": len(memory),
                },
            )

            context_vector = self.embedding_service.embed_task_context(task, history, memory)
            belief_state = self.state_service.get_belief_state(task_id)

            routing_result = self.router_service.route(
                context_vector=context_vector,
                belief_state=belief_state,
            )
            selected_agent = self.agent_registry.get_by_index(
                routing_result["selected_agent_index"]
            )
            trace.record(
                "sacm.route_agent",
                "chain",
                {"step": step_index + 1},
                {
                    "selected_agent": selected_agent.name,
                    "selected_agent_index": routing_result["selected_agent_index"],
                },
            )

            compiled_context = self.context_compiler.compile(
                task=task,
                agent=selected_agent,
                history=history,
                memory=memory,
            )
            agent_task = self.context_compiler.compile_v1(
                run_id=run_id or f"legacy:{task_id}",
                step_id=f"agent-{step_index + 1}",
                agent=selected_agent,
                context=compiled_context,
            )
            agent_result = selected_agent.run_v1(agent_task)
            result = selected_agent.result_from_v1(agent_result)

            self.event_service.save_agent_result(
                task_id,
                selected_agent.name,
                result,
                task_contract=agent_task,
                result_contract=agent_result,
            )
            self.memory_service.add_from_agent_result(task_id, result)
            self.state_service.update_belief_state(task_id, routing_result["next_belief"])

            done = self.verifier.is_done(task, result)
            trace.record(
                "sacm.agent_result",
                "chain",
                {"agent_name": selected_agent.name, "step": step_index + 1},
                {
                    "next_state": result.next_state_hint,
                    "confidence": result.confidence,
                    "verified": self.verifier.has_successful_verification(result),
                },
            )

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
        response = {
            "task_id": task_id,
            "status": final_task.status,
            "steps": steps_taken,
            "last_events": [event.payload for event in events],
        }
        trace.finish(
            {
                "status": response["status"],
                "steps": steps_taken,
                "event_count": len(response["last_events"]),
            }
        )
        return response
