from typing import Any

from sqlalchemy.orm import Session

from sacm.core.cost_service import CostService
from sacm.infrastructure.db.models import (
    ContextEvent,
    ExecutionPlan,
    JiraDeliveryState,
    Run,
)


class RunContextService:
    """Builds the operational context displayed for a durable run."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def build(
        self, run: Run, *, include_snapshot_metadata: bool = True
    ) -> dict[str, Any]:
        task = run.task
        project = run.project
        organization = project.organization if project else None
        agent_events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == task.id,
                ContextEvent.event_type == "agent_result",
            )
            .order_by(ContextEvent.created_at)
            .all()
        )
        execution_plan = (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.task_id == task.id)
            .order_by(ExecutionPlan.revision.desc(), ExecutionPlan.id)
            .first()
        )
        jira_delivery = (
            self.db.query(JiraDeliveryState)
            .filter(JiraDeliveryState.task_id == task.id)
            .first()
        )
        context = {
            "run": {
                "id": run.id,
                "workflow_version": run.workflow_version,
                "source_revision": run.source_revision,
                "target_repo_path": run.target_repo_path,
            },
            "task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "status": task.status,
                "target_repo_path": task.target_repo_path,
                "contract_version": task.contract_version,
                "connector_type": task.connector_type,
                "external_id": task.external_id,
                "external_url": task.external_url,
                "task_contract": task.task_contract,
                "readiness_score": task.readiness_score,
                "readiness_details": task.readiness_details,
                "clarifications": [
                    {
                        "id": item.id,
                        "field_name": item.field_name,
                        "question": item.question,
                        "status": item.status,
                        "answer": item.answer,
                        "created_at": item.created_at,
                        "answered_at": item.answered_at,
                    }
                    for item in task.clarifications
                ],
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            },
            "organization": (
                {
                    "id": organization.id,
                    "slug": organization.slug,
                    "name": organization.name,
                }
                if organization
                else None
            ),
            "project": (
                {
                    "id": project.id,
                    "slug": project.slug,
                    "name": project.name,
                    "repository_full_name": project.repository_full_name,
                    "repository_path": project.repository_path,
                }
                if project
                else None
            ),
            "agents": [self._agent_invocation(event) for event in agent_events],
            "costs": CostService(self.db).summarize_task(task.id),
            "application_context": self._application_context(task.application_context),
            "execution_plan": self._execution_plan(execution_plan),
            "jira_delivery": (
                {
                    "status": jira_delivery.status,
                    "jira_status": jira_delivery.jira_status,
                    "status_comment_id": jira_delivery.status_comment_id,
                    "pr_status": jira_delivery.pr_status,
                    "pr_url": jira_delivery.pr_url,
                    "context": jira_delivery.context,
                    "last_error": jira_delivery.last_error,
                    "updated_at": jira_delivery.updated_at,
                }
                if jira_delivery
                else None
            ),
        }
        if include_snapshot_metadata:
            from sacm.core.snapshot_service import SnapshotService

            snapshots = SnapshotService(self.db)
            if snapshots.available():
                context["snapshot"] = snapshots.latest_metadata(run.id)
                context["replay"] = snapshots.replay_metadata(run.id)
        return context

    @staticmethod
    def _application_context(application_context: Any) -> dict[str, Any] | None:
        if application_context is None:
            return None
        return {
            "id": application_context.id,
            "status": application_context.status,
            "scanner_version": application_context.scanner_version,
            "graph_hash": application_context.graph_hash,
            "impact_analysis": application_context.impact_analysis,
            "risk_analysis": application_context.risk_analysis,
            "repositories": [
                {
                    "position": repository.position,
                    "full_name": repository.full_name,
                    "requested_path": repository.requested_path,
                    "resolved_path": repository.resolved_path,
                    "base_revision": repository.base_revision,
                    "status": repository.status,
                    "error_code": repository.error_code,
                    "error_message": repository.error_message,
                    "file_count": repository.file_count,
                    "skipped_file_count": repository.skipped_file_count,
                }
                for repository in application_context.repositories
            ],
        }

    @staticmethod
    def _execution_plan(plan: ExecutionPlan | None) -> dict[str, Any] | None:
        if plan is None:
            return None
        security = plan.security_review
        return {
            "id": plan.id,
            "revision": plan.revision,
            "status": plan.status,
            "policy_pack": plan.policy_pack,
            "risk_decision": plan.risk_decision.decision,
            "policy_decision": plan.policy_decision.decision,
            "security_review": (
                {
                    "required": security.required,
                    "status": security.status,
                    "reviewer": security.reviewer_configuration,
                    "findings": security.findings,
                    "reviewed_at": security.reviewed_at,
                    "reviewed_by": security.reviewed_by,
                }
                if security is not None
                else None
            ),
            "approval_gates": [
                {
                    "id": gate.id,
                    "gate_type": gate.gate_type,
                    "action": gate.action,
                    "reason": gate.reason,
                    "status": gate.status,
                    "step_ids": gate.step_ids,
                    "approval_id": gate.approval_id,
                }
                for gate in plan.approval_gates
            ],
            "steps": [
                {
                    "id": step.id,
                    "sequence": step.sequence,
                    "kind": step.kind,
                    "title": step.title,
                    "objective": step.objective,
                    "acceptance_criteria": step.acceptance_criteria,
                    "required_tools": step.required_tools,
                    "risk_tags": step.risk_tags,
                    "depends_on": step.depends_on,
                    "agent": step.agent_configuration,
                }
                for step in plan.steps
            ],
        }

    @staticmethod
    def _agent_invocation(event: ContextEvent) -> dict[str, Any]:
        payload = event.payload
        task_contract = payload.get("agent_task_contract") or {}
        result_contract = payload.get("agent_result_contract") or {}
        return {
            "event_id": event.id,
            "name": payload.get("agent_name") or "unknown-agent",
            "role": task_contract.get("role"),
            "status": result_contract.get("status"),
            "summary": payload.get("summary"),
            "confidence": payload.get("confidence"),
            "next_state_hint": payload.get("next_state_hint"),
            "usage": payload.get("usage", []),
            "tool_execution": payload.get("tool_execution", []),
            "created_at": event.created_at,
        }
