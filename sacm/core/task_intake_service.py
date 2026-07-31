from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import ContextEvent, Task, TaskClarification
from sacm.schemas.task import JiraWebhook, ReadinessAssessment, TaskContractV1

READINESS_THRESHOLD = 0.8
READINESS_WEIGHTS = {
    "description": 0.25,
    "acceptance_criteria": 0.35,
    "repositories": 0.25,
    "requested_by": 0.15,
}
CLARIFICATION_QUESTIONS = {
    "description": "What problem should be solved and what behavior should change?",
    "acceptance_criteria": "What observable conditions must be met for acceptance?",
    "repositories": "Which repository or repositories should SACM operate on?",
    "requested_by": "Who owns the business decision and can clarify this task?",
}


class TaskIntakeService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def assess(contract: TaskContractV1) -> ReadinessAssessment:
        checks = {
            "description": bool(contract.description.strip()),
            "acceptance_criteria": bool(contract.acceptance_criteria),
            "repositories": bool(contract.repositories),
            "requested_by": bool(contract.requested_by),
        }
        score = round(
            sum(READINESS_WEIGHTS[field] for field, passed in checks.items() if passed),
            2,
        )
        return ReadinessAssessment(
            score=score,
            ready=score >= READINESS_THRESHOLD,
            missing_fields=[field for field, passed in checks.items() if not passed],
            checks=checks,
        )

    @staticmethod
    def from_jira(payload: JiraWebhook) -> TaskContractV1:
        fields = payload.issue.fields
        reporter = fields.reporter or {}
        priority = fields.priority or {}
        return TaskContractV1(
            connector_type="jira",
            external_id=payload.issue.key,
            external_url=payload.issue.self,
            title=fields.summary,
            description=_jira_description_text(fields.description),
            priority=priority.get("name"),
            labels=fields.labels,
            requested_by=reporter.get("accountId")
            or reporter.get("displayName")
            or reporter.get("emailAddress"),
            metadata={"webhook_event": payload.webhookEvent},
        )

    def ingest(
        self, contract: TaskContractV1
    ) -> tuple[Task, ReadinessAssessment, list[TaskClarification]]:
        existing = (
            self.db.query(Task)
            .filter(
                Task.connector_type == contract.connector_type,
                Task.external_id == contract.external_id,
            )
            .first()
        )
        if existing:
            assessment = ReadinessAssessment.model_validate(
                existing.readiness_details
            )
            return existing, assessment, list(existing.clarifications)

        assessment = self.assess(contract)
        repository_path = next(
            (reference.path for reference in contract.repositories if reference.path),
            None,
        )
        now = datetime.utcnow()
        task = Task(
            title=contract.title,
            description=contract.description,
            target_repo_path=repository_path,
            status="pending" if assessment.ready else "awaiting_clarification",
            contract_version=contract.schema_version,
            connector_type=contract.connector_type,
            external_id=contract.external_id,
            external_url=contract.external_url,
            task_contract=contract.model_dump(mode="json"),
            readiness_score=assessment.score,
            readiness_details=assessment.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
        self.db.add(task)
        self.db.flush()
        clarifications = [
            TaskClarification(
                task_id=task.id,
                field_name=field,
                question=CLARIFICATION_QUESTIONS[field],
            )
            for field in assessment.missing_fields
        ]
        self.db.add_all(clarifications)
        self.db.add(
            ContextEvent(
                task_id=task.id,
                event_type="task_contract_ingested",
                payload={
                    "connector_type": contract.connector_type,
                    "external_id": contract.external_id,
                    "readiness": assessment.model_dump(mode="json"),
                },
            )
        )
        self.db.commit()
        self.db.refresh(task)
        return task, assessment, clarifications

    def answer(
        self, task_id: str, clarification_id: str, answer: Any
    ) -> tuple[Task, ReadinessAssessment, list[TaskClarification]] | None:
        clarification = (
            self.db.query(TaskClarification)
            .filter(
                TaskClarification.id == clarification_id,
                TaskClarification.task_id == task_id,
            )
            .first()
        )
        if not clarification:
            return None
        task = clarification.task
        contract_data = dict(task.task_contract or {})
        contract_data[clarification.field_name] = answer
        try:
            contract = TaskContractV1.model_validate(contract_data)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid answer for '{clarification.field_name}': {exc.errors()[0]['msg']}"
            ) from exc
        assessment = self.assess(contract)
        clarification.answer = answer
        clarification.status = "answered"
        clarification.answered_at = datetime.utcnow()
        task.description = contract.description
        task.target_repo_path = next(
            (reference.path for reference in contract.repositories if reference.path),
            None,
        )
        task.task_contract = contract.model_dump(mode="json")
        task.readiness_score = assessment.score
        task.readiness_details = assessment.model_dump(mode="json")
        task.status = "pending" if assessment.ready else "awaiting_clarification"
        task.updated_at = datetime.utcnow()
        self.db.add(
            ContextEvent(
                task_id=task.id,
                event_type="task_clarification_answered",
                payload={
                    "field_name": clarification.field_name,
                    "readiness": assessment.model_dump(mode="json"),
                },
            )
        )
        self.db.commit()
        self.db.refresh(task)
        return task, assessment, list(task.clarifications)


def _jira_description_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            part for item in value if (part := _jira_description_text(item))
        )
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            return value["text"].strip()
        return _jira_description_text(value.get("content", []))
    return ""
