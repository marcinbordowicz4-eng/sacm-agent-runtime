import pytest

from sacm.core.task_intake_service import TaskIntakeService
from sacm.infrastructure.db.models import ContextEvent, Task
from sacm.schemas.task import JiraWebhook, RepositoryReference, TaskContractV1


def test_incomplete_contract_creates_clarifications(db):
    contract = TaskContractV1(
        connector_type="jira",
        external_id="SACM-42",
        title="Add governed task intake",
    )

    task, readiness, clarifications = TaskIntakeService(db).ingest(contract)

    assert task.status == "awaiting_clarification"
    assert readiness.score == 0
    assert {item.field_name for item in clarifications} == {
        "description",
        "acceptance_criteria",
        "repositories",
        "requested_by",
    }
    assert db.query(ContextEvent).filter_by(task_id=task.id).count() == 1


def test_answering_clarifications_makes_task_ready(db):
    service = TaskIntakeService(db)
    task, _, clarifications = service.ingest(
        TaskContractV1(
            connector_type="linear",
            external_id="ENG-7",
            title="Persist task contracts",
        )
    )
    answers = {
        "description": "Persist normalized task contracts for governed execution.",
        "acceptance_criteria": ["Contract and readiness are queryable."],
        "repositories": [RepositoryReference(path="/repos/sacm").model_dump()],
        "requested_by": "platform-team",
    }

    for clarification in clarifications:
        result = service.answer(
            task.id, clarification.id, answers[clarification.field_name]
        )
        assert result is not None

    persisted = db.get(Task, task.id)
    assert persisted is not None
    assert persisted.status == "pending"
    assert persisted.readiness_score == 1
    assert persisted.target_repo_path == "/repos/sacm"


def test_connector_external_id_is_idempotent(db):
    service = TaskIntakeService(db)
    contract = TaskContractV1(
        connector_type="github",
        external_id="owner/repo#12",
        title="Test intake",
    )

    first, _, _ = service.ingest(contract)
    second, _, _ = service.ingest(contract)

    assert first.id == second.id
    assert db.query(Task).count() == 1


def test_invalid_clarification_answer_is_rejected(db):
    service = TaskIntakeService(db)
    task, _, clarifications = service.ingest(
        TaskContractV1(
            connector_type="jira",
            external_id="SACM-99",
            title="Validate clarification answers",
        )
    )
    repositories = next(
        item for item in clarifications if item.field_name == "repositories"
    )

    with pytest.raises(ValueError, match="Invalid answer for 'repositories'"):
        service.answer(task.id, repositories.id, "not-a-list")


def test_jira_webhook_maps_adf_description(db):
    service = TaskIntakeService(db)
    payload = JiraWebhook.model_validate(
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "SACM-123",
                "self": "https://jira.example/rest/api/3/issue/SACM-123",
                "fields": {
                    "summary": "Govern agent execution",
                    "description": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Require approval before deployment.",
                                    }
                                ],
                            }
                        ],
                    },
                    "labels": ["sacm"],
                    "priority": {"name": "High"},
                    "reporter": {
                        "accountId": "jira-user-42",
                        "displayName": "Owner",
                    },
                },
            },
        }
    )

    contract = service.from_jira(payload)
    task, readiness, _ = service.ingest(contract)

    assert contract.description == "Require approval before deployment."
    assert contract.requested_by == "jira-user-42"
    assert task.connector_type == "jira"
    assert readiness.missing_fields == ["acceptance_criteria", "repositories"]
