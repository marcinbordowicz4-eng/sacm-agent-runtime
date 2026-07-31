from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sacm.connectors.jira import JiraTransport, adf_document
from sacm.core.jira_orchestration_service import JiraOrchestrationService
from sacm.core.jira_service import JiraService
from sacm.infrastructure.db.models import (
    Base,
    Membership,
    Organization,
    Project,
    TaskClarification,
)
from sacm.schemas.jira import JiraConnectorCreate


class DemoJiraTransport(JiraTransport):
    """Clearly simulated Jira Cloud transport used only by the offline demo."""

    def __init__(self) -> None:
        self.comments: list[dict[str, Any]] = []
        self.status = "To Do"

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: float,
    ) -> httpx.Response:
        request = httpx.Request(method, url)
        if url.endswith("/comment?maxResults=100"):
            return httpx.Response(200, request=request, json={"comments": self.comments})
        if "/comment/" in url and method == "PUT":
            comment_id = url.rsplit("/", 1)[-1]
            comment = next(item for item in self.comments if item["id"] == comment_id)
            comment["body"] = (json or {})["body"]
            return httpx.Response(200, request=request, json=comment)
        if url.endswith("/comment") and method == "POST":
            comment = {
                "id": str(len(self.comments) + 1),
                "body": (json or {})["body"],
            }
            self.comments.append(comment)
            return httpx.Response(201, request=request, json=comment)
        if url.endswith("/transitions") and method == "GET":
            return httpx.Response(
                200,
                request=request,
                json={
                    "transitions": [
                        {"id": "1", "name": "In Progress", "to": {"name": "In Progress"}},
                        {"id": "2", "name": "Done", "to": {"name": "Done"}},
                    ]
                },
            )
        if url.endswith("/transitions") and method == "POST":
            self.status = "Done" if (json or {}).get("transition", {}).get("id") == "2" else "In Progress"
            return httpx.Response(204, request=request)
        return httpx.Response(
            200,
            request=request,
            json={"fields": {"status": {"name": self.status}}},
        )


def run_demo() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2] / "examples" / "jira-e2e-demo"
    repositories = [
        root / "repos" / "storefront",
        root / "repos" / "payments",
        root / "repos" / "orders",
    ]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    organization = Organization(slug="jira-demo", name="Jira E2E Demo")
    db.add(organization)
    db.flush()
    project = Project(
        organization_id=organization.id,
        slug="shop",
        name="Shop Delivery",
        repository_path=str(repositories[0]),
    )
    db.add_all(
        [
            project,
            Membership(
                organization_id=organization.id,
                actor_id="jira-demo-admin",
                role="admin",
            ),
        ]
    )
    db.commit()
    transport = DemoJiraTransport()
    service = JiraService(
        db,
        transport=transport,
        secret_resolver=lambda _: "SIMULATED-JIRA-TOKEN",
    )
    connector = service.create_connector(
        JiraConnectorCreate.model_validate(
            {
                "organization_id": organization.id,
                "project_id": project.id,
                "base_url": "https://simulated-jira.invalid",
                "jira_project_key": "SHOP",
                "username": "demo@example.invalid",
                "api_token_ref": "env:SIMULATED_ONLY",
                "field_mapping": {
                    "acceptance_criteria": "customfield_10001",
                    "repositories": "customfield_10002",
                },
                "status_mapping": {
                    "AWAITING_CLARIFICATION": "To Do",
                    "WAITING_FOR_EXECUTOR": "In Progress",
                    "EXECUTION_QUEUED": "In Progress",
                    "COMPLETED": "Done",
                },
            },
        ),
        actor="jira-demo-admin",
    )
    issue = {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "id": "10042",
            "key": "SHOP-42",
            "fields": {
                "project": {"key": "SHOP"},
                "summary": "Coordinate checkout payment contract change",
                "description": adf_document(
                    "Update checkout, payment authorization, and order persistence."
                ),
                "customfield_10001": None,
                "customfield_10002": [
                    {"path": str(path), "base_revision": "demo-fixture"}
                    for path in repositories
                ],
                "labels": ["sacm", "checkout"],
                "priority": {"name": "High"},
                "reporter": {"accountId": "demo-product-owner"},
                "status": {"id": "1", "name": "To Do"},
            },
        },
    }
    initial = service.process_webhook(connector, issue, delivery_id="demo-created")
    clarification = (
        db.query(TaskClarification)
        .filter(
            TaskClarification.task_id == initial.task_id,
            TaskClarification.field_name == "acceptance_criteria",
        )
        .one()
    )
    answer = service.process_webhook(
        connector,
        {
            "webhookEvent": "comment_created",
            "issue": {"key": "SHOP-42"},
            "comment": {
                "id": "answer-1",
                "body": adf_document(
                    "[SACM-CLARIFICATION:v1 "
                    f"clarification_id={clarification.id}]\n"
                    '["Checkout contract is versioned", '
                    '"Payment authorization remains idempotent", '
                    '"Orders schema migration is covered by tests"]'
                ),
            },
        },
        delivery_id="demo-comment",
    )
    delivery = JiraOrchestrationService(db, jira=service).orchestrate(
        connector,
        initial.task_id or "",
        actor="jira-demo-admin",
        create_pull_request=False,
    )
    return {
        "external_services": "SIMULATED (offline fake transports)",
        "task_id": initial.task_id,
        "initial_readiness": initial.readiness_score,
        "clarified_readiness": answer.readiness_score,
        "ready": answer.readiness_ready,
        "impact_repositories": delivery.details.get("impact_risk"),
        "plan": delivery.details.get("plan"),
        "run_id": delivery.run_id,
        "delivery_status": delivery.status,
        "evidence_status": "pending real executor completion",
        "pr_status": delivery.pr_status,
        "jira_status_comments": len(transport.comments),
    }
