import os
import time

from sqlalchemy.orm import Session

from sacm.core.agent_registry import AgentRegistry
from sacm.core.context_compiler import ContextCompiler
from sacm.core.context_engine_service import ContextEngineService
from sacm.core.draft_pull_request_service import DraftPullRequestService
from sacm.core.embedding_service import EmbeddingService
from sacm.core.event_service import EventService
from sacm.core.evidence_service import EvidenceService
from sacm.core.feedback_service import FeedbackService
from sacm.core.memory_service import MemoryService
from sacm.core.observability import ObservabilityService
from sacm.core.outcome_router_service import OutcomeRouterService
from sacm.core.router import RouterService
from sacm.core.state_service import StateService
from sacm.core.task_run_lease_service import TaskRunLeaseService
from sacm.core.task_service import TaskService
from sacm.core.verifier import Verifier
from sacm.schemas.application_context import ContextExpansionRequest

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
        self.context_engine = ContextEngineService(db)
        self.router_service = RouterService()
        self.verifier = Verifier(db)
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

        lease_service = TaskRunLeaseService(self.db)
        owner_token = lease_service.acquire(task_id)
        started_at = time.monotonic()
        try:
            lease_service.heartbeat(task_id, owner_token)
            self._save_progress(
                task_id,
                run_id,
                phase="run",
                status="started",
                step=0,
                started_at=started_at,
                task_status=task.status,
            )
            response = self._run_task_locked(
                task_id,
                max_steps=max_steps,
                run_id=run_id,
                recovery_context=recovery_context,
                lease_service=lease_service,
                owner_token=owner_token,
                started_at=started_at,
            )
            self._save_progress(
                task_id,
                run_id,
                phase="run",
                status="finished",
                step=response["steps"],
                started_at=started_at,
                task_status=response["status"],
            )
            return response
        except Exception as exc:
            if not self.db.is_active:
                self.db.rollback()
            try:
                current = self.task_service.get(task_id)
                self._save_progress(
                    task_id,
                    run_id,
                    phase="run",
                    status="failed",
                    step=None,
                    started_at=started_at,
                    task_status=current.status if current else None,
                    error_type=exc.__class__.__name__,
                    error=str(exc),
                )
            except Exception:
                self.db.rollback()
            raise
        finally:
            lease_service.release(task_id, owner_token)

    def _run_task_locked(
        self,
        task_id: str,
        *,
        max_steps: int,
        run_id: str | None,
        recovery_context: dict | None,
        lease_service: TaskRunLeaseService,
        owner_token: str,
        started_at: float,
    ) -> dict:
        task = self.task_service.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if self._should_initialize_planning(task.status):
            initialized = self.task_service.update_status(
                task_id,
                "planning",
                expected_status=task.status,
                expected_updated_at=task.updated_at,
            )
            if not initialized:
                raise RuntimeError(
                    f"Task {task_id} changed while the orchestrator was starting."
                )
        steps_taken = 0
        verification_complete = False
        delivery_result = None
        trace = self.observability.start_task(task_id, max_steps)

        for step_index in range(max_steps):
            steps_taken = step_index + 1
            task = self.task_service.get(task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found during execution")
            expected_task_status = task.status
            expected_task_updated_at = task.updated_at

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
            phase_agent = self._phase_agent_name(task.status)
            if phase_agent and not recovery_action:
                selected_agent = (
                    self.agent_registry.get(phase_agent) or selected_agent
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

            diagnostic_bundle = recovery_failure.get("diagnostic_bundle") or {}
            failure_details = recovery_failure.get("details") or {}
            context_request = ContextExpansionRequest(
                run_id=run_id,
                step_id=f"agent-{step_index + 1}",
                role=selected_agent.contract_role,
                reason=(
                    "recovery_context_expansion"
                    if recovery_action == "EXPAND_CONTEXT"
                    else "task_execution"
                ),
                changed_symbols=list(
                    diagnostic_bundle.get("changed_symbols")
                    or failure_details.get("changed_symbols")
                    or []
                ),
                failing_symbols=[
                    item.get("message", "")
                    for item in diagnostic_bundle.get("compiler_diagnostics", [])
                    if item.get("message")
                ],
                changed_files=list(failure_details.get("changed_files") or []),
                failed_tests=[
                    item.get("test_name", "")
                    for item in diagnostic_bundle.get("failed_tests", [])
                    if item.get("test_name")
                ],
                affected_requirements=list(
                    diagnostic_bundle.get("affected_requirements")
                    or failure_details.get("affected_requirements")
                    or []
                ),
                max_depth=3 if recovery_action == "EXPAND_CONTEXT" else 2,
                max_nodes=96 if recovery_action == "EXPAND_CONTEXT" else 48,
            )
            context_package = self.context_engine.build(task_id, context_request)
            compiled_context = self.context_compiler.compile(
                task=task,
                agent=selected_agent,
                history=history,
                memory=memory,
                context_package=context_package,
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
            lease_service.heartbeat(task_id, owner_token)
            self._save_progress(
                task_id,
                run_id,
                phase=expected_task_status,
                status="agent_started",
                agent=selected_agent.name,
                step=step_index + 1,
                started_at=started_at,
                task_status=expected_task_status,
            )
            with lease_service.guard(task_id, owner_token):
                agent_result = selected_agent.run_v1(agent_task)
            result = selected_agent.result_from_v1(agent_result)
            lease_service.heartbeat(task_id, owner_token)
            if not self.task_service.is_current(
                task_id,
                expected_status=expected_task_status,
                expected_updated_at=expected_task_updated_at,
            ):
                raise RuntimeError(
                    f"Task {task_id} changed during agent execution; "
                    "the stale orchestrator result was not applied."
                )
            self._save_progress(
                task_id,
                run_id,
                phase=expected_task_status,
                status="agent_finished",
                agent=selected_agent.name,
                step=step_index + 1,
                started_at=started_at,
                task_status=expected_task_status,
            )

            self.event_service.save_agent_result(
                task_id,
                selected_agent.name,
                result,
                task_contract=agent_task,
                result_contract=agent_result,
            )
            self.memory_service.add_from_agent_result(task_id, result)
            self.state_service.update_belief_state(task_id, routing_result["next_belief"])

            lease_service.heartbeat(task_id, owner_token)
            self._save_progress(
                task_id,
                run_id,
                phase=expected_task_status,
                status="verification_started",
                agent=selected_agent.name,
                step=step_index + 1,
                started_at=started_at,
                task_status=expected_task_status,
            )
            with lease_service.guard(task_id, owner_token) as heartbeat:
                verification = self.verifier.evaluate(
                    task,
                    result,
                    run_id=run_id,
                )
                heartbeat.check()
                self.event_service.save(
                    task_id,
                    "verification_matrix_v2",
                    verification.model_dump(mode="json"),
                )
                if (
                    verification.strict
                    and verification.technical_complete
                    and run_id is not None
                ):
                    evidence = EvidenceService(self.db)
                    provisional_pack = evidence.build(
                        run_id,
                        trusted_internal=True,
                    )
                    heartbeat.check()
                    provisional_result = evidence.verify(
                        run_id,
                        provisional_pack.id,
                        trusted_internal=True,
                    )
                    heartbeat.check()
                    verification = self.verifier.finalize_evidence(
                        verification,
                        evidence_valid=provisional_result.status != "INVALID",
                    )
                    self.event_service.save(
                        task_id,
                        "verification_matrix_v2",
                        verification.model_dump(mode="json"),
                    )
                    if verification.complete:
                        final_pack = evidence.build(
                            run_id,
                            trusted_internal=True,
                        )
                        heartbeat.check()
                        final_result = evidence.verify(
                            run_id,
                            final_pack.id,
                            trusted_internal=True,
                        )
                        heartbeat.check()
                        if final_result.status == "INVALID":
                            verification = self.verifier.finalize_evidence(
                                verification,
                                evidence_valid=False,
                            )
                            self.event_service.save(
                                task_id,
                                "verification_matrix_v2",
                                verification.model_dump(mode="json"),
                            )
            done = verification.complete
            verification_complete = done
            lease_service.heartbeat(task_id, owner_token)
            self._save_progress(
                task_id,
                run_id,
                phase=expected_task_status,
                status="verification_finished",
                agent=selected_agent.name,
                step=step_index + 1,
                started_at=started_at,
                task_status=expected_task_status,
                verification_complete=done,
            )
            trace.record(
                "sacm.agent_result",
                "chain",
                {"agent_name": selected_agent.name, "step": step_index + 1},
                {
                    "next_state": result.next_state_hint,
                    "confidence": result.confidence,
                    "verified": done,
                    "verification_strict": verification.strict,
                    "verification_blockers": verification.blocking_reasons,
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
                delivery_service = DraftPullRequestService(self.db)
                with lease_service.guard(task_id, owner_token):
                    delivery_result = delivery_service.publish(
                        task_id,
                        verified=True,
                        run_id=run_id,
                    )
                lease_service.heartbeat(task_id, owner_token)
                delivery_service.record(task_id, delivery_result)
                updated = self.task_service.mark_done(
                    task_id,
                    expected_status=expected_task_status,
                    expected_updated_at=expected_task_updated_at,
                )
                if not updated:
                    raise RuntimeError(
                        f"Task {task_id} changed before verified completion; "
                        "the stale orchestrator result was not applied."
                    )
                break

            if result.next_state_hint:
                updated = self.task_service.update_status(
                    task_id,
                    self._unverified_next_state(result.next_state_hint),
                    expected_status=expected_task_status,
                    expected_updated_at=expected_task_updated_at,
                )
                if not updated:
                    raise RuntimeError(
                        f"Task {task_id} changed before phase transition; "
                        "the stale orchestrator result was not applied."
                    )

        final_task = self.task_service.get(task_id)
        if not final_task:
            raise ValueError(f"Task {task_id} missing after execution")
        if final_task.status == "done" and not verification_complete:
            repaired = self.task_service.update_status(
                task_id,
                "testing",
                expected_status=final_task.status,
                expected_updated_at=final_task.updated_at,
            )
            if not repaired:
                raise RuntimeError(
                    f"Task {task_id} changed while preventing unverified completion."
                )
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
            "delivery_status": (
                delivery_result.get("status") if delivery_result else None
            ),
            "delivery": delivery_result,
        }
        trace.finish(
            {
                "status": response["status"],
                "steps": steps_taken,
                "event_count": len(last_events),
            }
        )
        return response

    def _save_progress(
        self,
        task_id: str,
        run_id: str | None,
        *,
        phase: str,
        status: str,
        step: int | None,
        started_at: float,
        agent: str | None = None,
        task_status: str | None = None,
        **details,
    ) -> None:
        self.event_service.save(
            task_id,
            "workflow_progress",
            {
                "schema_version": "workflow-progress/v1",
                "task_id": task_id,
                "run_id": run_id,
                "phase": phase,
                "status": status,
                "task_status": task_status,
                "agent": agent,
                "step": step,
                "elapsed_ms": int((time.monotonic() - started_at) * 1_000),
                **details,
            },
        )

    @staticmethod
    def _phase_agent_name(current_status: str) -> str | None:
        return {
            "coding": "CodexExecutor",
            "testing": "CloudExecutor",
            "reviewing": "Reviewer",
        }.get(current_status)

    @staticmethod
    def _unverified_next_state(next_state_hint: str) -> str:
        return "testing" if next_state_hint == "done" else next_state_hint

    @staticmethod
    def _should_initialize_planning(current_status: str) -> bool:
        return current_status == "pending"
