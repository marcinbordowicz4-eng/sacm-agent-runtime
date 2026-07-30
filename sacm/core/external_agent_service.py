from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.event_service import EventService
from sacm.core.policy_service import PolicyService
from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import Approval, RunStep
from sacm.schemas.contracts import (
    AgentResultV1,
    AgentTaskV1,
    ExternalAgentStepCreate,
)
from sacm.schemas.result import AgentResult


@dataclass(frozen=True)
class ExternalAgentStep:
    framework: str
    agent_name: str
    step: RunStep
    task: AgentTaskV1


@dataclass(frozen=True)
class ExternalAgentSubmission:
    step: RunStep
    approval_id: str | None = None


class ExternalAgentService:
    """Framework-neutral bridge into SACM's governed run contracts."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.runs = RunService(db)

    def schedule(
        self, run_id: str, payload: ExternalAgentStepCreate
    ) -> ExternalAgentStep:
        request = payload.model_dump(mode="json")
        step = self.runs.add_step(
            run_id,
            f"{payload.framework}:{payload.agent_name}",
            {"external_agent_request": request},
            payload.idempotency_key,
        )
        stored_request = step.input_.get("external_agent_request")
        if stored_request != request:
            raise ValueError(
                "The idempotency key is already used by a different agent task."
            )

        task_data = step.input_.get("agent_task")
        if task_data is None:
            task = AgentTaskV1(
                run_id=run_id,
                step_id=step.id,
                role=payload.role,
                objective=payload.objective,
                acceptance_criteria=payload.acceptance_criteria,
                context_references=payload.context_references,
                allowed_tools=payload.allowed_tools,
                denied_tools=payload.denied_tools,
                token_budget=payload.token_budget,
                cost_budget_usd=payload.cost_budget_usd,
                timeout_seconds=payload.timeout_seconds,
                execution_context=payload.execution_context,
            )
            step.input_ = {
                **step.input_,
                "framework": payload.framework,
                "agent_name": payload.agent_name,
                "agent_task": task.model_dump(mode="json"),
            }
            self.db.commit()
            self.db.refresh(step)
        else:
            task = AgentTaskV1.model_validate(task_data)

        return ExternalAgentStep(
            framework=payload.framework,
            agent_name=payload.agent_name,
            step=step,
            task=task,
        )

    def submit(
        self, run_id: str, step_id: str, result: AgentResultV1
    ) -> ExternalAgentSubmission:
        step = self.runs.get_step(run_id, step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found for run {run_id}.")
        task_data = step.input_.get("agent_task")
        if task_data is None:
            raise ValueError("The step is not an external agent contract step.")
        task = AgentTaskV1.model_validate(task_data)
        if result.run_id != run_id or result.step_id != step_id:
            raise ValueError("Agent result run_id and step_id must match the endpoint.")

        output = result.model_dump(mode="json")
        if step.status == "COMPLETED":
            if self._result_output(step.output) != output:
                raise ValueError("The completed step already has a different result.")
            return ExternalAgentSubmission(
                step=step,
                approval_id=(step.output or {}).get("sacm_approval_id"),
            )
        if step.status == "FAILED":
            failure = result.failure or {
                "type": "ExternalAgentFailure",
                "message": result.summary,
            }
            if result.status != "FAILED" or (step.output or {}).get("failure") != failure:
                raise ValueError("The failed step cannot accept a different result.")
            return ExternalAgentSubmission(step=step)

        approval = self._approval_for(step)
        if step.status == "AWAITING_APPROVAL":
            if approval is None:
                raise ValueError("The step is missing its approval record.")
            if approval.status == "PENDING":
                raise ValueError(f"Approval {approval.id} is still pending.")
            if approval.status == "REJECTED":
                failed = self.runs.fail_step(
                    run_id,
                    step_id,
                    {
                        "type": "ApprovalRejected",
                        "approval_id": approval.id,
                        "reason": approval.decision_reason,
                    },
                )
                return ExternalAgentSubmission(step=failed, approval_id=approval.id)
        self._persist_result(step, task, result)
        if result.status == "NEEDS_APPROVAL":
            resource = {
                "step_id": step.id,
                "framework": step.input_["framework"],
                "agent_name": step.input_["agent_name"],
                "summary": result.summary,
                "actions": result.actions,
            }
            approval = PolicyService(self.db).request_approval(
                run_id, "external_agent_result", resource
            )
            output["sacm_approval_id"] = approval.id
            waiting = self.runs.await_step_approval(run_id, step_id, output)
            return ExternalAgentSubmission(step=waiting, approval_id=approval.id)
        if result.status == "FAILED":
            failure = result.failure or {
                "type": "ExternalAgentFailure",
                "message": result.summary,
            }
            return ExternalAgentSubmission(
                step=self.runs.fail_step(run_id, step_id, failure)
            )
        if approval:
            output["sacm_approval_id"] = approval.id
        return ExternalAgentSubmission(
            step=self.runs.complete_step(run_id, step_id, output),
            approval_id=approval.id if approval else None,
        )

    def _persist_result(
        self, step: RunStep, task: AgentTaskV1, result: AgentResultV1
    ) -> None:
        artifacts: list[dict[str, Any]] = []
        for artifact in [*result.artifacts, *result.evidence]:
            payload = self._artifact_payload(artifact.model_dump(mode="json"))
            if payload not in artifacts:
                artifacts.append(payload)
        artifacts.extend(
            {"type": "usage", **usage.model_dump(mode="json")}
            for usage in result.usage
        )
        legacy_result = AgentResult(
            agent_name=str(step.input_["agent_name"]),
            summary=result.summary,
            actions=result.actions,
            artifacts=artifacts,
            confidence=result.confidence,
            next_state_hint=result.next_state_hint,
            memory_update=result.memory_update,
            skills_contributed=result.skills_contributed,
        )
        EventService(self.db).save_agent_result(
            step.run.task_id,
            f"{step.input_['framework']}:{step.input_['agent_name']}",
            legacy_result,
            task_contract=task,
            result_contract=result,
        )

    def _approval_for(self, step: RunStep) -> Approval | None:
        approval_id = (step.output or {}).get("sacm_approval_id")
        return self.db.get(Approval, approval_id) if approval_id else None

    @staticmethod
    def _result_output(output: dict[str, Any] | None) -> dict[str, Any]:
        result = dict(output or {})
        result.pop("sacm_approval_id", None)
        return result

    @staticmethod
    def _artifact_payload(artifact: dict[str, Any]) -> dict[str, Any]:
        metadata = artifact.pop("metadata")
        return {
            "type": artifact.pop("artifact_type"),
            **{key: value for key, value in artifact.items() if value is not None},
            **metadata,
        }
