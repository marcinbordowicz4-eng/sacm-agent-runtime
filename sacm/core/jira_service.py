import json
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from sacm.connectors.jira import (
    JiraCloudClient,
    JiraError,
    JiraTransport,
    SecretResolver,
    adf_document,
    adf_text,
    canonical_hash,
    environment_secret_resolver,
    verify_webhook_signature,
)
from sacm.core.task_intake_service import TaskIntakeService
from sacm.core.tenancy_service import TenancyService
from sacm.infrastructure.db.models import (
    ContextEvent,
    JiraConnector,
    JiraConnectorOperation,
    JiraDeliveryState,
    JiraWebhookDelivery,
    Project,
    Task,
    TaskClarification,
)
from sacm.schemas.jira import JiraConnectorCreate, JiraWebhookResult
from sacm.schemas.task import RepositoryReference, TaskContractV1

STATUS_MARKER = "SACM-STATUS:v1"
CLARIFICATION_MARKER = re.compile(
    r"^\[SACM-CLARIFICATION:v1 clarification_id="
    r"([0-9a-fA-F-]{36})\]\s*\n([\s\S]+)$"
)


class JiraService:
    def __init__(
        self,
        db: Session,
        *,
        transport: JiraTransport | None = None,
        secret_resolver: SecretResolver = environment_secret_resolver,
    ) -> None:
        self.db = db
        self.transport = transport
        self.secret_resolver = secret_resolver

    def create_connector(
        self, payload: JiraConnectorCreate, *, actor: str
    ) -> JiraConnector:
        project = self.db.get(Project, payload.project_id)
        if project is None or project.organization_id != payload.organization_id:
            raise ValueError("Project does not belong to the connector organization.")
        TenancyService(self.db).require_project_permission(
            project.id,
            actor,
            "tasks.write",
            resource_type="jira_connector",
            resource_id=payload.jira_project_key,
        )
        existing = (
            self.db.query(JiraConnector)
            .filter(
                JiraConnector.organization_id == payload.organization_id,
                JiraConnector.jira_project_key == payload.jira_project_key,
            )
            .first()
        )
        connector = existing or JiraConnector()
        connector.organization_id = payload.organization_id
        connector.project_id = payload.project_id
        connector.base_url = str(payload.base_url).rstrip("/")
        connector.jira_project_key = payload.jira_project_key
        connector.username = payload.username
        connector.api_token_ref = payload.api_token_ref
        connector.webhook_secret_ref = payload.webhook_secret_ref
        connector.field_mapping = payload.field_mapping
        connector.status_mapping = payload.status_mapping
        connector.timeout_seconds = payload.timeout_seconds
        connector.max_attempts = payload.max_attempts
        connector.enabled = True
        self.db.add(connector)
        self.db.commit()
        self.db.refresh(connector)
        TenancyService(self.db).audit_sensitive(
            project.organization_id,
            project.id,
            actor,
            "jira.connector.configure",
            "jira_connector",
            connector.id,
            "Jira connector configured with secret references.",
            {"jira_project_key": connector.jira_project_key},
        )
        return connector

    def verify_signature(
        self, connector: JiraConnector, body: bytes, signature: str | None
    ) -> bool:
        secret = (
            self.secret_resolver(connector.webhook_secret_ref)
            if connector.webhook_secret_ref
            else None
        )
        return verify_webhook_signature(body, signature, secret)

    def process_webhook(
        self,
        connector: JiraConnector,
        payload: dict[str, Any],
        *,
        delivery_id: str,
    ) -> JiraWebhookResult:
        payload_hash = canonical_hash(payload)
        existing = (
            self.db.query(JiraWebhookDelivery)
            .filter(
                JiraWebhookDelivery.connector_id == connector.id,
                JiraWebhookDelivery.delivery_id == delivery_id,
            )
            .first()
        )
        event_type = str(
            payload.get("webhookEvent")
            or payload.get("issue_event_type_name")
            or "unknown"
        )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise ValueError("Jira delivery ID was reused with a different payload.")
            task = self.db.get(Task, existing.task_id) if existing.task_id else None
            return JiraWebhookResult(
                duplicate=True,
                event_type=existing.event_type,
                task_id=existing.task_id,
                readiness_score=task.readiness_score if task else None,
                readiness_ready=(
                    bool((task.readiness_details or {}).get("ready")) if task else None
                ),
            )
        delivery = JiraWebhookDelivery(
            connector_id=connector.id,
            organization_id=connector.organization_id,
            project_id=connector.project_id,
            delivery_id=delivery_id,
            event_type=event_type,
            payload_hash=payload_hash,
        )
        self.db.add(delivery)
        self.db.commit()
        try:
            if "comment" in event_type.lower() or payload.get("comment"):
                result = self._process_comment(connector, payload)
                delivery.task_id = result.task_id
            else:
                task, readiness, _ = TaskIntakeService(self.db).ingest(
                    self.normalize_issue(connector, payload)
                )
                self._delivery_state(connector, task)
                result = JiraWebhookResult(
                    event_type=event_type,
                    task_id=task.id,
                    readiness_score=readiness.score,
                    readiness_ready=readiness.ready,
                )
                delivery.task_id = task.id
            delivery.status = "PROCESSED"
            delivery.processed_at = datetime.utcnow()
            self.db.commit()
            TenancyService(self.db).audit_sensitive(
                connector.organization_id,
                connector.project_id,
                f"jira-webhook:{connector.id}",
                "jira.webhook.process",
                "jira_webhook_delivery",
                delivery.id,
                "Authenticated Jira webhook delivery processed.",
                {
                    "delivery_id": delivery.delivery_id,
                    "event_type": delivery.event_type,
                    "task_id": delivery.task_id,
                },
            )
            return result
        except Exception as exc:
            delivery.status = "FAILED"
            delivery.error = self._safe_error(exc)
            delivery.processed_at = datetime.utcnow()
            self.db.commit()
            raise

    def normalize_issue(
        self, connector: JiraConnector, payload: dict[str, Any]
    ) -> TaskContractV1:
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            raise ValueError("Jira webhook does not contain an issue.")
        fields = issue.get("fields")
        if not isinstance(fields, dict):
            raise ValueError("Jira issue does not contain fields.")
        key = str(issue.get("key") or "").strip()
        project_key = str(
            (fields.get("project") or {}).get("key")
            if isinstance(fields.get("project"), dict)
            else ""
        )
        if not key or project_key != connector.jira_project_key:
            raise ValueError("Jira issue is not mapped to this connector project.")
        mapping = connector.field_mapping or {}
        acceptance_value = fields.get(mapping.get("acceptance_criteria", ""))
        repository_value = fields.get(mapping.get("repositories", ""))
        project = self.db.get(Project, connector.project_id)
        repositories = self._repositories(repository_value)
        if not repositories and project is not None and (
            project.repository_full_name or project.repository_path
        ):
            repositories = [
                RepositoryReference(
                    full_name=project.repository_full_name,
                    path=project.repository_path,
                )
            ]
        reporter: dict[str, Any] = (
            fields["reporter"] if isinstance(fields.get("reporter"), dict) else {}
        )
        priority: dict[str, Any] = (
            fields["priority"] if isinstance(fields.get("priority"), dict) else {}
        )
        labels = fields.get("labels")
        status: dict[str, Any] = (
            fields["status"] if isinstance(fields.get("status"), dict) else {}
        )
        custom_metadata = {
            logical_name: fields.get(field_id)
            for logical_name, field_id in sorted(mapping.items())
            if logical_name.startswith("metadata.") and isinstance(field_id, str)
        }
        return TaskContractV1(
            connector_type="jira",
            external_id=key,
            external_url=f"{connector.base_url}/browse/{key}",
            project_id=connector.project_id,
            title=str(fields.get("summary") or "").strip(),
            description=adf_text(fields.get("description")),
            acceptance_criteria=self._acceptance_criteria(acceptance_value),
            repositories=repositories,
            priority=str(priority.get("name") or "") or None,
            labels=[
                str(item)
                for item in labels
                if isinstance(item, (str, int, float))
            ]
            if isinstance(labels, list)
            else [],
            requested_by=str(
                reporter.get("accountId")
                or reporter.get("displayName")
                or reporter.get("emailAddress")
                or ""
            )
            or None,
            metadata={
                "source": "jira_cloud",
                "connector_id": connector.id,
                "issue_id": issue.get("id"),
                "jira_project_key": project_key,
                "jira_status": status.get("name"),
                "jira_status_id": status.get("id"),
                "webhook_event": payload.get("webhookEvent"),
                "custom_fields": custom_metadata,
            },
        )

    def sync_status(
        self,
        connector: JiraConnector,
        task: Task,
        summary: dict[str, Any],
        *,
        target_state: str,
        run_id: str | None = None,
    ) -> JiraDeliveryState:
        state = self._delivery_state(connector, task)
        state.run_id = run_id or state.run_id
        rendered = self._status_text(task, summary, target_state)
        marker = f"[{STATUS_MARKER} task_id={task.id}]"
        body = adf_document(f"{marker}\n{rendered}")
        payload_hash = canonical_hash(body)
        operation = self._operation(
            connector,
            task,
            run_id,
            "STATUS_COMMENT",
            f"status-comment:{task.id}:{payload_hash}",
            payload_hash,
        )
        if operation.status != "COMPLETED":
            client = self._client(connector)
            comments = client.comments(task.external_id or "")
            comment = next(
                (
                    item
                    for item in comments
                    if marker in adf_text(item.get("body"))
                ),
                None,
            )
            try:
                operation.attempt_count += 1
                if comment:
                    result = client.update_comment(
                        task.external_id or "", str(comment["id"]), body
                    )
                else:
                    try:
                        result = client.create_comment(task.external_id or "", body)
                    except JiraError:
                        reconciled = next(
                            (
                                item
                                for item in client.comments(task.external_id or "")
                                if marker in adf_text(item.get("body"))
                            ),
                            None,
                        )
                        if reconciled is None:
                            raise
                        result = reconciled
                operation.external_id = str(result.get("id") or "")
                operation.result = {"comment_id": operation.external_id}
                operation.status = "COMPLETED"
                operation.completed_at = datetime.utcnow()
                operation.error = None
                state.status_comment_id = operation.external_id
            except Exception as exc:
                operation.status = "FAILED"
                operation.error = self._safe_error(exc)
                state.last_error = operation.error
                self.db.commit()
                raise
        jira_status = self._transition(connector, task, target_state, run_id)
        state.status = target_state
        if jira_status:
            state.jira_status = jira_status
        state.context = summary
        state.last_error = None
        self.db.add(
            ContextEvent(
                task_id=task.id,
                organization_id=task.organization_id,
                project_id=task.project_id,
                tenant_attribution=task.tenant_attribution,
                data_region=task.data_region,
                data_classification=task.data_classification,
                event_type="jira_status_synchronized",
                payload={
                    "connector_id": connector.id,
                    "delivery_status": target_state,
                    "jira_status": jira_status,
                    "status_comment_id": state.status_comment_id,
                    "run_id": run_id,
                },
            )
        )
        self.db.commit()
        self.db.refresh(state)
        return state

    def _transition(
        self,
        connector: JiraConnector,
        task: Task,
        target_state: str,
        run_id: str | None,
    ) -> str | None:
        target = (connector.status_mapping or {}).get(target_state)
        if not target:
            return None
        operation = self._operation(
            connector,
            task,
            run_id,
            "STATUS_TRANSITION",
            f"transition:{task.id}:{target_state}:{target}",
            canonical_hash({"target": target}),
        )
        if operation.status == "COMPLETED":
            return str((operation.result or {}).get("status") or target)
        client = self._client(connector)
        issue = client.issue(task.external_id or "")
        current = (
            ((issue.get("fields") or {}).get("status") or {}).get("name")
            if isinstance(issue.get("fields"), dict)
            else None
        )
        if current == target:
            operation.status = "COMPLETED"
            operation.result = {"status": target, "reconciled": True}
            operation.completed_at = datetime.utcnow()
            return target
        transitions = client.transitions(task.external_id or "")
        transition = next(
            (
                item
                for item in transitions
                if str(item.get("id")) == target
                or str(item.get("name", "")).casefold() == target.casefold()
                or str((item.get("to") or {}).get("name", "")).casefold()
                == target.casefold()
            ),
            None,
        )
        if transition is None:
            raise JiraError("Configured Jira transition is not currently available.")
        try:
            operation.attempt_count += 1
            client.transition(task.external_id or "", str(transition["id"]))
        except JiraError:
            issue = client.issue(task.external_id or "")
            current = ((issue.get("fields") or {}).get("status") or {}).get("name")
            if current != target:
                raise
        operation.status = "COMPLETED"
        operation.result = {"status": target}
        operation.completed_at = datetime.utcnow()
        return target

    def _process_comment(
        self, connector: JiraConnector, payload: dict[str, Any]
    ) -> JiraWebhookResult:
        issue = payload.get("issue") or {}
        task = (
            self.db.query(Task)
            .filter(
                Task.connector_type == "jira",
                Task.external_id == issue.get("key"),
                Task.project_id == connector.project_id,
            )
            .first()
        )
        if task is None:
            raise ValueError("Jira comment does not belong to an ingested task.")
        comment = payload.get("comment") or {}
        text = adf_text(comment.get("body"))
        match = CLARIFICATION_MARKER.fullmatch(text.strip())
        if match is None:
            return JiraWebhookResult(event_type=str(payload.get("webhookEvent")), task_id=task.id)
        clarification_id, answer_text = match.groups()
        clarification = self.db.get(TaskClarification, clarification_id)
        if clarification is None or clarification.task_id != task.id:
            raise ValueError("Clarification marker does not match this Jira task.")
        answer: Any = answer_text.strip()
        if clarification.field_name in {"acceptance_criteria", "repositories"}:
            try:
                answer = json.loads(answer)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{clarification.field_name} clarification requires JSON."
                ) from exc
        result = TaskIntakeService(self.db).answer(task.id, clarification.id, answer)
        if result is None:
            raise ValueError("Clarification not found.")
        updated, readiness, _ = result
        self.db.add(
            ContextEvent(
                task_id=task.id,
                organization_id=task.organization_id,
                project_id=task.project_id,
                tenant_attribution=task.tenant_attribution,
                data_region=task.data_region,
                data_classification=task.data_classification,
                event_type="jira_clarification_answered",
                payload={
                    "clarification_id": clarification.id,
                    "jira_comment_id": comment.get("id"),
                },
            )
        )
        self.db.commit()
        return JiraWebhookResult(
            event_type=str(payload.get("webhookEvent")),
            task_id=updated.id,
            readiness_score=readiness.score,
            readiness_ready=readiness.ready,
            clarification_id=clarification.id,
        )

    def _delivery_state(
        self, connector: JiraConnector, task: Task
    ) -> JiraDeliveryState:
        state = (
            self.db.query(JiraDeliveryState)
            .filter(JiraDeliveryState.task_id == task.id)
            .first()
        )
        if state is None:
            state = JiraDeliveryState(
                connector_id=connector.id,
                organization_id=connector.organization_id,
                project_id=connector.project_id,
                task_id=task.id,
                status="INGESTED",
            )
            self.db.add(state)
            self.db.commit()
            self.db.refresh(state)
        return state

    def _operation(
        self,
        connector: JiraConnector,
        task: Task,
        run_id: str | None,
        operation_type: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> JiraConnectorOperation:
        operation = (
            self.db.query(JiraConnectorOperation)
            .filter(
                JiraConnectorOperation.connector_id == connector.id,
                JiraConnectorOperation.idempotency_key == idempotency_key,
            )
            .first()
        )
        if operation:
            if operation.payload_hash != payload_hash:
                raise ValueError("Jira operation idempotency payload mismatch.")
            return operation
        operation = JiraConnectorOperation(
            id=str(uuid.uuid4()),
            connector_id=connector.id,
            organization_id=connector.organization_id,
            project_id=connector.project_id,
            task_id=task.id,
            run_id=run_id,
            operation_type=operation_type,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        self.db.add(operation)
        self.db.commit()
        return operation

    def _client(self, connector: JiraConnector) -> JiraCloudClient:
        return JiraCloudClient(
            base_url=connector.base_url,
            username=connector.username,
            api_token_ref=connector.api_token_ref,
            secret_resolver=self.secret_resolver,
            transport=self.transport,
            timeout_seconds=connector.timeout_seconds,
            max_attempts=connector.max_attempts,
        )

    @staticmethod
    def _acceptance_criteria(value: Any) -> list[str]:
        if isinstance(value, list):
            return [
                text
                for item in value
                if (text := adf_text(item).strip())
            ]
        text = adf_text(value)
        return [
            item.strip(" -*\t")
            for item in text.splitlines()
            if item.strip(" -*\t")
        ]

    @staticmethod
    def _repositories(value: Any) -> list[RepositoryReference]:
        if not isinstance(value, list):
            value = [value] if value else []
        references: list[RepositoryReference] = []
        for item in value:
            if isinstance(item, dict):
                full_name = item.get("full_name")
                path = item.get("path")
                base_revision = item.get("base_revision")
            elif isinstance(item, str):
                full_name = item if "/" in item and not item.startswith("/") else None
                path = item if full_name is None else None
                base_revision = None
            else:
                continue
            if full_name or path:
                references.append(
                    RepositoryReference(
                        full_name=full_name,
                        path=path,
                        base_revision=base_revision,
                    )
                )
        return references

    @staticmethod
    def _status_text(
        task: Task, summary: dict[str, Any], target_state: str
    ) -> str:
        readiness = task.readiness_details or {}
        sections = [
            "SACM delivery status",
            f"State: {target_state}",
            (
                f"Definition of Ready: {round(float(task.readiness_score or 0) * 100)}% "
                f"(missing: {', '.join(readiness.get('missing_fields', [])) or 'none'})"
            ),
            f"Questions: {summary.get('questions', 'none')}",
            f"Application impact/risk: {summary.get('impact_risk', 'not built')}",
            f"Plan/policy/approvals: {summary.get('plan', 'not built')}",
            f"Run/agents/tests/evidence: {summary.get('run', 'not started')}",
            f"Pull request: {summary.get('pull_request', 'not configured')}",
        ]
        return "\n".join(sections)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, JiraError):
            return str(exc)
        return exc.__class__.__name__
