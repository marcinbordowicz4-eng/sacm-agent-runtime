import hashlib
import hmac
import json
import os
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.run_service import RunService
from sacm.infrastructure.db.models import GitHubWebhookDelivery, Project
from sacm.schemas.run import RunCreate


class GitHubWebhookService:
    """Creates durable runs only for authenticated, explicitly mapped issue labels."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def verify_signature(body: bytes, signature: str | None) -> bool:
        secret = os.getenv("SACM_GITHUB_WEBHOOK_SECRET")
        if not secret or not signature:
            return False
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def create_run(
        self, payload: dict[str, Any], *, delivery_id: str | None = None
    ) -> str | None:
        if delivery_id:
            existing = (
                self.db.query(GitHubWebhookDelivery)
                .filter(GitHubWebhookDelivery.delivery_id == delivery_id)
                .first()
            )
            if existing:
                return existing.run_id
        if payload.get("action") != "labeled":
            return None
        issue = payload.get("issue")
        repository = payload.get("repository")
        label = payload.get("label", {}).get("name")
        if not isinstance(issue, dict) or not isinstance(repository, dict):
            return None
        if issue.get("pull_request"):
            return None
        if label != os.getenv("SACM_GITHUB_TRIGGER_LABEL", "sacm"):
            return None
        project = self._project(repository.get("full_name"))
        if not project:
            return None
        title = str(issue.get("title") or "").strip()
        body = str(issue.get("body") or "").strip()
        if not title:
            return None
        run = RunService(self.db).create(
            RunCreate(
                title=title[:255],
                description=body or title,
                target_repo_path=project.repository_path,
                source_revision=repository.get("default_branch"),
                project_id=project.id,
            )
        )
        if delivery_id:
            self.db.add(
                GitHubWebhookDelivery(
                    delivery_id=delivery_id,
                    run_id=run.id,
                )
            )
            self.db.commit()
        return run.id

    def _project(self, full_name: object) -> Project | None:
        if not isinstance(full_name, str):
            return None
        project = (
            self.db.query(Project)
            .filter(Project.repository_full_name == full_name)
            .first()
        )
        if project:
            return project
        try:
            mappings = json.loads(os.getenv("SACM_GITHUB_REPOSITORIES_JSON", "{}"))
        except json.JSONDecodeError:
            return None
        path = mappings.get(full_name) if isinstance(mappings, dict) else None
        if not isinstance(path, str) or not path:
            return None
        return Project(
            id=f"legacy:{full_name}",
            organization_id="legacy",
            slug=full_name.replace("/", "-"),
            name=full_name,
            repository_full_name=full_name,
            repository_path=path,
        )
