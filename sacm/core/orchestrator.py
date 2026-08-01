import os

from sqlalchemy.orm import Session

from sacm.core.agent_registry import AgentRegistry
from sacm.core.context_compiler import ContextCompiler
from sacm.core.embedding_service import EmbeddingService
from sacm.core.event_service import EventService
from sacm.core.feedback_service import FeedbackService
from sacm.core.memory_service import MemoryService
from sacm.core.observability import ObservabilityService
from sacm.core.outcome_router_service import OutcomeRouterService
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
        self.outcome_router = OutcomeRouterService(
            db,
            registry=self.agent_registry,
            neural_router=self.router_service,
        )

    def run_task(
        self,
        task_id: str,
        max_steps: int = MAX_STEPS,
        run_id: str | None = None,
        recovery_context: dict | None = None,
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
            recovery_action = (
                (recovery_context or {}).get("decision", {}).get("action")
            )
            recovery_failure = (recovery_context or {}).get("failure", {})
            query = task.description
            if recovery_failure.get("message"):
                query = f"{query}\nRecovery failure: {recovery_failure['message']}"
            top_k = 16 if recovery_action == "EXPAND_CONTEXT" else 8
            memory = self.memory_service.search(task_id, query, top_k=top_k)
            trace.record(
                "sacm.retrieve_memory",
                "retriever",
                {"task_id": task_id, "query_length": len(query), "top_k": top_k},
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
            risk_level = (
                str(task.application_context.risk_analysis.get("level"))
                if task.application_context
                and task.application_context.risk_analysis.get("level")
                else None
            )
            outcome_decision = self.outcome_router.rank(
                task,
                context_vector,
                belief_state,
                risk_level=risk_level,
                previous_failure_classification=(
                    recovery_failure.get("classification")
                ),
                neural_result=routing_result,
            )
            selected_agent = self.agent_registry.get(
                outcome_decision.selected_agent_name
            ) or self.agent_registry.get_by_index(
                routing_result["selected_agent_index"]
            )
            if recovery_action == "REPLAN":
                selected_agent = next(
                    (
                        agent
                        for agent in self.agent_registry.all()
                        if agent.contract_role == "reasoner"
                    ),
                    selected_agent,
                )
            elif recovery_action == "SWITCH_MODEL":
                selected_agent = self.agent_registry.get_by_index(
                    routing_result["selected_agent_index"]
                    + int((recovery_context or {}).get("decision", {}).get("attempt", 1))
                )
            executed_agent_index = self.agent_registry.names().index(
                selected_agent.name
            )
            trace.record(
                "sacm.route_agent",
                "chain",
                {"step": step_index + 1},
                {
                    "selected_agent": selected_agent.name,
                    "selected_agent_index": executed_agent_index,
                    "router_strategy": outcome_decision.strategy,
                    "router_score": next(
                        candidate.score
                        for candidate in outcome_decision.candidates
                        if candidate.agent_name == selected_agent.name
                    ),
                },
            )
            self.event_service.save(
                task_id,
                "router_decision",
                {
                    "schema_version": outcome_decision.schema_version,
                    "selected_agent_name": selected_agent.name,
                    "strategy": outcome_decision.strategy,
                    "minimum_samples": outcome_decision.minimum_samples,
                    "risk_level": outcome_decision.risk_level,
                    "task_tags": outcome_decision.task_tags,
                    "fallback_reason": outcome_decision.fallback_reason,
                    "outcome_semantics": outcome_decision.outcome_semantics,
                },
            )

            compiled_context = self.context_compiler.compile(
                task=task,
                agent=selected_agent,
                history=history,
                memory=memory,
            )
            if recovery_context:
                decision = recovery_context["decision"]
                compiled_context.constraints = [
                    f"Recovery action: {decision['action']}",
                    *decision.get("instructions", []),
                    *compiled_context.constraints,
                ]
                compiled_context.previous_findings = [
                    f"Previous failure: {recovery_failure.get('message', 'unknown')}",
                    *compiled_context.previous_findings,
                ]
            agent_task = self.context_compiler.compile_v1(
                run_id=run_id or f"legacy:{task_id}",
                step_id=f"agent-{step_index + 1}",
                agent=selected_agent,
                context=compiled_context,
            )
            if recovery_context:
                agent_task.execution_context["recovery"] = recovery_context
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
                selected_agent_index=executed_agent_index,
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
        last_events = [event.payload for event in events]
        response = {
            "task_id": task_id,
            "status": final_task.status,
            "steps": steps_taken,
            "last_events": last_events,
        }
        trace.finish(
            {
                "status": response["status"],
                "steps": steps_taken,
                "event_count": len(last_events),
            }
        )
        return response
