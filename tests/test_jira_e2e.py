import base64
import hashlib
import hmac
from pathlib import Path
from typing import Any

import httpx
import pytest

from sacm.connectors.jira import JiraCloudClient, adf_document, adf_text
from sacm.core.jira_orchestration_service import JiraOrchestrationService
from sacm.core.jira_service import JiraService
from sacm.core.tenancy_service import AuthorizationError
from sacm.demo.jira_e2e import DemoJiraTransport, run_demo
from sacm.infrastructure.db.models import (
    JiraConnector,
    JiraConnectorOperation,
    Membership,
    Organization,
    Project,
    Task,
    TaskClarification,
)


def _tenant(db, tmp_path: Path, suffix: str = "one"):
    organization = Organization(slug=f"org-{suffix}", name=f"Org {suffix}")
    db.add(organization)
    db.flush()
    project = Project(
        organization_id=organization.id,
        slug=f"project-{suffix}",
        name=f"Project {suffix}",
        repository_path=str(tmp_path),
    )
    db.add_all(
        [
            project,
            Membership(
                organization_id=organization.id,
                actor_id=f"admin-{suffix}",
                role="admin",
            ),
        ]
    )
    db.flush()
    connector = JiraConnector(
        organization_id=organization.id,
        project_id=project.id,
        base_url="https://jira.example.invalid",
        jira_project_key=f"DEMO{suffix.upper()}",
        username="jira@example.invalid",
        api_token_ref="env:JIRA_TOKEN",
        webhook_secret_ref="env:JIRA_WEBHOOK_SECRET",
        field_mapping={
            "acceptance_criteria": "customfield_1",
            "repositories": "customfield_2",
        },
        status_mapping={
            "AWAITING_CLARIFICATION": "To Do",
            "WAITING_FOR_EXECUTOR": "In Progress",
        },
    )
    db.add(connector)
    db.commit()
    return organization, project, connector


def _issue(connector: JiraConnector, repo: Path) -> dict[str, Any]:
    return {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "id": "100",
            "key": f"{connector.jira_project_key}-1",
            "fields": {
                "project": {"key": connector.jira_project_key},
                "summary": "Change API contract and database schema",
                "description": adf_document("Coordinate checkout and payment changes."),
                "customfield_1": None,
                "customfield_2": [{"path": str(repo)}],
                "labels": ["sacm"],
                "priority": {"name": "High"},
                "reporter": {"accountId": "owner-1"},
                "status": {"name": "To Do"},
            },
        },
    }


def _service(db, transport=None):
    return JiraService(
        db,
        transport=transport or DemoJiraTransport(),
        secret_resolver=lambda reference: (
            "webhook-value" if "WEBHOOK" in reference else "token-value"
        ),
    )


def test_signature_and_basic_auth_are_verified_without_persisting_token(
    db, tmp_path
):
    _, _, connector = _tenant(db, tmp_path)
    service = _service(db)
    body = b'{"issue":"demo"}'
    signature = hmac.new(b"webhook-value", body, hashlib.sha256).hexdigest()
    assert service.verify_signature(connector, body, f"sha256={signature}")
    assert not service.verify_signature(connector, body, "sha256=bad")
    assert "token-value" not in repr(connector.__dict__)

    captured = {}

    class Capture:
        def request(self, method, url, *, headers, json, timeout):
            captured.update(headers)
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={"fields": {}},
            )

    JiraCloudClient(
        base_url=connector.base_url,
        username=connector.username,
        api_token_ref=connector.api_token_ref,
        secret_resolver=lambda _: "token-value",
        transport=Capture(),
    ).issue("DEMO-1")
    assert base64.b64decode(
        captured["Authorization"].removeprefix("Basic ")
    ).decode() == "jira@example.invalid:token-value"
    assert "token-value" not in str(db.query(JiraConnectorOperation).all())


def test_adf_custom_fields_delivery_dedupe_and_project_association(db, tmp_path):
    _, project, connector = _tenant(db, tmp_path)
    service = _service(db)
    first = service.process_webhook(
        connector, _issue(connector, tmp_path), delivery_id="delivery-1"
    )
    duplicate = service.process_webhook(
        connector, _issue(connector, tmp_path), delivery_id="delivery-1"
    )
    task = db.get(Task, first.task_id)
    assert first.readiness_score == 0.65
    assert duplicate.duplicate
    assert task.project_id == project.id
    assert task.description == "Coordinate checkout and payment changes."
    assert task.task_contract["priority"] == "High"
    assert task.task_contract["labels"] == ["sacm"]
    assert task.task_contract["repositories"][0]["path"] == str(tmp_path)
    assert task.task_contract["metadata"]["jira_status"] == "To Do"


def test_clarification_marker_updates_only_the_durable_clarification(db, tmp_path):
    _, _, connector = _tenant(db, tmp_path)
    service = _service(db)
    result = service.process_webhook(
        connector, _issue(connector, tmp_path), delivery_id="delivery-1"
    )
    clarification = (
        db.query(TaskClarification)
        .filter(TaskClarification.task_id == result.task_id)
        .one()
    )
    answer = service.process_webhook(
        connector,
        {
            "webhookEvent": "comment_created",
            "issue": {"key": f"{connector.jira_project_key}-1"},
            "comment": {
                "id": "comment-1",
                "body": adf_document(
                    "[SACM-CLARIFICATION:v1 "
                    f"clarification_id={clarification.id}]\n"
                    '["API remains backwards compatible", "Migration has a rollback"]'
                ),
            },
        },
        delivery_id="delivery-2",
    )
    assert answer.readiness_ready
    assert clarification.status == "answered"
    assert clarification.answer == [
        "API remains backwards compatible",
        "Migration has a rollback",
    ]

    with pytest.raises(ValueError, match="does not match"):
        service.process_webhook(
            connector,
            {
                "webhookEvent": "comment_created",
                "issue": {"key": f"{connector.jira_project_key}-1"},
                "comment": {
                    "id": "comment-2",
                    "body": adf_document(
                        "[SACM-CLARIFICATION:v1 "
                        "clarification_id=00000000-0000-0000-0000-000000000000]\n"
                        '{"project_id":"attacker"}'
                    ),
                },
            },
            delivery_id="delivery-3",
        )


def test_status_comment_is_single_and_recovers_after_lost_create_response(
    db, tmp_path
):
    _, _, connector = _tenant(db, tmp_path)

    class LostResponse(DemoJiraTransport):
        lost = False

        def request(self, method, url, *, headers, json, timeout):
            if url.endswith("/comment") and method == "POST" and not self.lost:
                self.lost = True
                super().request(
                    method, url, headers=headers, json=json, timeout=timeout
                )
                raise httpx.ReadTimeout("simulated lost response")
            return super().request(
                method, url, headers=headers, json=json, timeout=timeout
            )

    transport = LostResponse()
    service = _service(db, transport)
    result = service.process_webhook(
        connector, _issue(connector, tmp_path), delivery_id="delivery-1"
    )
    task = db.get(Task, result.task_id)
    summary = {"questions": "acceptance_criteria"}
    service.sync_status(
        connector, task, summary, target_state="AWAITING_CLARIFICATION"
    )
    service.sync_status(
        connector, task, summary, target_state="AWAITING_CLARIFICATION"
    )
    assert len(transport.comments) == 1
    assert "SACM-STATUS:v1" in adf_text(transport.comments[0]["body"])
    operation = (
        db.query(JiraConnectorOperation)
        .filter(JiraConnectorOperation.operation_type == "STATUS_COMMENT")
        .one()
    )
    assert operation.status == "COMPLETED"


def test_cross_tenant_access_and_incomplete_executor_pr_states(db, tmp_path):
    _, _, connector = _tenant(db, tmp_path, "one")
    _, _, _ = _tenant(db, tmp_path, "two")
    service = _service(db)
    result = service.process_webhook(
        connector, _issue(connector, tmp_path), delivery_id="delivery-1"
    )
    clarification = db.query(TaskClarification).filter_by(task_id=result.task_id).one()
    service.process_webhook(
        connector,
        {
            "webhookEvent": "comment_created",
            "issue": {"key": f"{connector.jira_project_key}-1"},
            "comment": {
                "id": "answer",
                "body": adf_document(
                    "[SACM-CLARIFICATION:v1 "
                    f"clarification_id={clarification.id}]\n"
                    '["Tests cover the contract"]'
                ),
            },
        },
        delivery_id="delivery-2",
    )
    with pytest.raises(AuthorizationError):
        JiraOrchestrationService(db, jira=service).orchestrate(
            connector, result.task_id or "", actor="admin-two"
        )
    delivery = JiraOrchestrationService(db, jira=service).orchestrate(
        connector,
        result.task_id or "",
        actor="admin-one",
        create_pull_request=False,
    )
    assert delivery.status == "WAITING_FOR_EXECUTOR"
    assert delivery.pr_status == "PR_NOT_CONFIGURED"
    assert delivery.run_id
    assert not delivery.execution_job_ids


def test_full_offline_jira_e2e_demo_uses_real_sacm_services():
    result = run_demo()
    assert result["external_services"].startswith("SIMULATED")
    assert result["initial_readiness"] == 0.65
    assert result["clarified_readiness"] == 1.0
    assert result["ready"] is True
    assert "3 repositories" in result["impact_repositories"]
    assert result["run_id"]
    assert result["delivery_status"] == "WAITING_FOR_EXECUTOR"
    assert result["evidence_status"] == "pending real executor completion"
    assert result["pr_status"] == "PR_NOT_CONFIGURED"
