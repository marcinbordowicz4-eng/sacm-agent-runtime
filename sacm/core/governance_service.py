from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from sacm.core.auth_service import production_mode
from sacm.core.tenancy_service import TenancyService
from sacm.infrastructure.db.models import (
    Artifact,
    AuditExportBatch,
    BackupRecord,
    ContextEvent,
    DataGovernancePolicy,
    DataGovernancePolicyRule,
    EvidencePack,
    ExecutorRegistration,
    GovernanceLegalHold,
    GovernanceRequest,
    GovernanceRequestItem,
    MemoryChunk,
    Organization,
    Project,
    Run,
    RunOutcomeAnalytics,
    RuntimeEvent,
    SIEMDelivery,
    SIEMSink,
    Task,
    TenantAuditEvent,
)
from sacm.schemas.governance import (
    AuditExportCreate,
    GovernanceLegalHoldCreate,
    GovernancePolicyCreate,
    GovernanceRequestCreate,
    SIEMSinkCreate,
    SIEMSinkUpdate,
)

RESOURCE_CATEGORIES = (
    "source_context",
    "task_metadata",
    "runtime_events",
    "logs",
    "artifacts",
    "evidence",
    "backups",
    "analytics",
    "audit",
)
CLASSIFICATIONS = ("Public", "Internal", "Confidential", "Restricted")
_SENSITIVE_KEYS = {
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
    ).encode()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _reference_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).lower() for part in _SENSITIVE_KEYS)
                else _sanitize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and (
        value.startswith("sacm_service_") or "-----BEGIN PRIVATE KEY-----" in value
    ):
        return "[REDACTED]"
    return value


class ResidencyService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def rule(
        self,
        organization_id: str,
        project_id: str | None,
        category: str,
    ) -> DataGovernancePolicyRule | None:
        for scope_project in (project_id, None):
            query = (
                self.db.query(DataGovernancePolicyRule)
                .join(DataGovernancePolicy)
                .filter(
                    DataGovernancePolicy.organization_id == organization_id,
                    DataGovernancePolicy.status == "ACTIVE",
                    DataGovernancePolicyRule.resource_category == category,
                )
            )
            query = (
                query.filter(DataGovernancePolicy.project_id == scope_project)
                if scope_project
                else query.filter(DataGovernancePolicy.project_id.is_(None))
            )
            rule = query.order_by(DataGovernancePolicy.version.desc()).first()
            if rule:
                return rule
        return None

    def resolve(
        self,
        *,
        organization_id: str,
        project_id: str | None,
        category: str,
        region: str | None = None,
        classification: str | None = None,
        storage_class: str | None = None,
    ) -> dict[str, str | None]:
        if category not in RESOURCE_CATEGORIES:
            raise ValueError("Unknown governance resource category.")
        project = self.db.get(Project, project_id) if project_id else None
        if project and project.organization_id != organization_id:
            raise ValueError("Project not found.")
        organization = self.db.get(Organization, organization_id)
        if organization is None:
            raise ValueError("Organization not found.")
        rule = self.rule(organization_id, project_id, category)
        resolved_region = (
            region
            or (project.data_region if project else None)
            or organization.data_region
            or os.getenv("SACM_DEFAULT_DATA_REGION")
            or (rule.allowed_regions[0] if rule and rule.allowed_regions else None)
        )
        resolved_classification = (
            classification
            or (rule.classification if rule else None)
            or (project.data_classification if project else None)
            or organization.data_classification
            or "Confidential"
        )
        resolved_storage_class = storage_class or (
            rule.storage_classes[0] if rule and rule.storage_classes else "standard"
        )
        if resolved_classification not in CLASSIFICATIONS:
            raise ValueError("Invalid data classification.")
        if rule:
            if resolved_region and resolved_region not in rule.allowed_regions:
                raise ValueError(
                    f"Region {resolved_region} is not allowed for {category}."
                )
            if resolved_storage_class not in rule.storage_classes:
                raise ValueError(
                    f"Storage class {resolved_storage_class} is not allowed for {category}."
                )
        return {
            "region": resolved_region,
            "classification": resolved_classification,
            "storage_class": resolved_storage_class,
        }

    def backfill(self, organization_id: str, actor: str) -> dict[str, Any]:
        TenancyService(self.db).require_permission(
            organization_id,
            actor,
            "data.manage",
            resource_type="governance_backfill",
            resource_id=organization_id,
        )
        organization = self.db.get(Organization, organization_id)
        if organization is None:
            raise ValueError("Organization not found.")
        default_region = organization.data_region or os.getenv("SACM_DEFAULT_DATA_REGION")
        default_classification = organization.data_classification or "Confidential"
        updated: dict[str, int] = {}
        tenant_models: tuple[type[Any], ...] = (Project, Task, Run)
        for model in tenant_models:
            query = self.db.query(model).filter(model.organization_id == organization_id)
            count = 0
            for raw_record in query:
                record = cast(Any, raw_record)
                if record.data_region is None and default_region:
                    record.data_region = default_region
                    count += 1
                if record.data_classification is None:
                    record.data_classification = default_classification
                    count += 1
            updated[model.__tablename__] = count
        storage_models: tuple[tuple[type[Any], str], ...] = (
            (Artifact, "artifacts"),
            (EvidencePack, "evidence"),
            (BackupRecord, "backups"),
            (ExecutorRegistration, "artifacts"),
        )
        for model, category in storage_models:
            query = self.db.query(model).filter(model.organization_id == organization_id)
            count = 0
            for raw_record in query:
                record = cast(Any, raw_record)
                metadata = self.resolve(
                    organization_id=organization_id,
                    project_id=getattr(record, "project_id", None),
                    category=category,
                    region=record.storage_region,
                    classification=record.storage_classification,
                    storage_class=record.storage_class,
                )
                for field, value in (
                    ("storage_region", metadata["region"]),
                    ("storage_classification", metadata["classification"]),
                    ("storage_class", metadata["storage_class"]),
                ):
                    if getattr(record, field) is None:
                        setattr(record, field, value)
                        count += 1
            updated[model.__tablename__] = count
        self.db.commit()
        TenancyService(self.db).audit_sensitive(
            organization_id,
            None,
            actor,
            "governance.metadata.backfill",
            "organization",
            organization_id,
            "Governance region and classification metadata backfilled.",
            {"updated": updated},
        )
        return {"organization_id": organization_id, "updated": updated}


class GovernancePolicyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tenancy = TenancyService(db)

    def create(
        self, organization_id: str, payload: GovernancePolicyCreate, actor: str
    ) -> DataGovernancePolicy:
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            project_id=payload.project_id,
            resource_type="data_governance_policy",
            resource_id=organization_id,
        )
        self._validate_project(organization_id, payload.project_id)
        scope_key = self._scope_key(organization_id, payload.project_id)
        latest = (
            self.db.query(DataGovernancePolicy)
            .filter(DataGovernancePolicy.scope_key == scope_key)
            .order_by(DataGovernancePolicy.version.desc())
            .first()
        )
        policy = DataGovernancePolicy(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            project_id=payload.project_id,
            scope_key=scope_key,
            name=payload.name,
            description=payload.description,
            version=(latest.version + 1 if latest else 1),
            status="DRAFT",
            created_by=actor,
        )
        self.db.add(policy)
        self.db.flush()
        for rule in payload.rules:
            self.db.add(
                DataGovernancePolicyRule(
                    id=str(uuid.uuid4()),
                    policy_id=policy.id,
                    resource_category=rule.resource_category,
                    classification=rule.classification,
                    retention_days=rule.retention_days,
                    legal_hold=rule.legal_hold,
                    deletion_mode=rule.deletion_mode,
                    exportable=rule.exportable,
                    allowed_regions=sorted(set(rule.allowed_regions)),
                    storage_classes=sorted(set(rule.storage_classes)),
                    evidence_preservation=rule.evidence_preservation,
                    metadata_=_sanitize(rule.metadata),
                )
            )
        self.db.commit()
        self.db.refresh(policy)
        self._audit(policy, actor, "governance.policy.create", "Policy draft created.")
        return policy

    def activate(
        self, organization_id: str, policy_id: str, actor: str
    ) -> DataGovernancePolicy:
        policy = self._authorized(organization_id, policy_id, actor)
        if policy.status != "DRAFT":
            raise ValueError("Only draft policies can be activated.")
        rules = self.rules(policy.id)
        missing = set(RESOURCE_CATEGORIES).difference(
            rule.resource_category for rule in rules
        )
        if missing:
            raise ValueError(
                f"Policy is missing resource categories: {', '.join(sorted(missing))}."
            )
        now = _utcnow()
        active = (
            self.db.query(DataGovernancePolicy)
            .filter(
                DataGovernancePolicy.scope_key == policy.scope_key,
                DataGovernancePolicy.status == "ACTIVE",
            )
            .all()
        )
        for prior in active:
            prior.status = "RETIRED"
            prior.retired_at = now
        policy.status = "ACTIVE"
        policy.activated_by = actor
        policy.activated_at = now
        target = (
            self.db.get(Project, policy.project_id)
            if policy.project_id
            else self.db.get(Organization, organization_id)
        )
        if target:
            target.governance_metadata = {
                "active_policy_id": policy.id,
                "active_policy_version": policy.version,
                "activated_at": now.isoformat(),
            }
        self.db.commit()
        self.db.refresh(policy)
        self._audit(policy, actor, "governance.policy.activate", "Policy activated.")
        return policy

    def get(
        self, organization_id: str, policy_id: str, actor: str
    ) -> DataGovernancePolicy:
        return self._authorized(organization_id, policy_id, actor)

    def retire(
        self, organization_id: str, policy_id: str, actor: str
    ) -> DataGovernancePolicy:
        policy = self._authorized(organization_id, policy_id, actor)
        if policy.status != "ACTIVE":
            raise ValueError("Only active policies can be retired.")
        policy.status = "RETIRED"
        policy.retired_at = _utcnow()
        self.db.commit()
        self.db.refresh(policy)
        self._audit(policy, actor, "governance.policy.retire", "Policy retired.")
        return policy

    def list_policies(
        self, organization_id: str, actor: str, project_id: str | None = None
    ) -> list[DataGovernancePolicy]:
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            project_id=project_id,
            resource_type="data_governance_policy",
            resource_id=organization_id,
        )
        query = self.db.query(DataGovernancePolicy).filter(
            DataGovernancePolicy.organization_id == organization_id
        )
        if project_id:
            query = query.filter(DataGovernancePolicy.project_id == project_id)
        return query.order_by(DataGovernancePolicy.created_at.desc()).all()

    def rules(self, policy_id: str) -> list[DataGovernancePolicyRule]:
        return (
            self.db.query(DataGovernancePolicyRule)
            .filter(DataGovernancePolicyRule.policy_id == policy_id)
            .order_by(DataGovernancePolicyRule.resource_category)
            .all()
        )

    def _authorized(
        self, organization_id: str, policy_id: str, actor: str
    ) -> DataGovernancePolicy:
        policy = (
            self.db.query(DataGovernancePolicy)
            .filter(
                DataGovernancePolicy.id == policy_id,
                DataGovernancePolicy.organization_id == organization_id,
            )
            .first()
        )
        if policy is None:
            raise ValueError("Governance policy not found.")
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            project_id=policy.project_id,
            resource_type="data_governance_policy",
            resource_id=policy.id,
        )
        return policy

    def _validate_project(
        self, organization_id: str, project_id: str | None
    ) -> None:
        if project_id:
            project = self.db.get(Project, project_id)
            if project is None or project.organization_id != organization_id:
                raise ValueError("Project not found.")

    def _audit(
        self, policy: DataGovernancePolicy, actor: str, action: str, reason: str
    ) -> None:
        self.tenancy.audit_sensitive(
            policy.organization_id,
            policy.project_id,
            actor,
            action,
            "data_governance_policy",
            policy.id,
            reason,
            {"version": policy.version, "status": policy.status},
        )

    @staticmethod
    def _scope_key(organization_id: str, project_id: str | None) -> str:
        return (
            f"organization:{organization_id}:project:{project_id}"
            if project_id
            else f"organization:{organization_id}"
        )


class GovernanceRequestService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tenancy = TenancyService(db)
        self.residency = ResidencyService(db)

    def create(
        self, organization_id: str, payload: GovernanceRequestCreate, actor: str
    ) -> GovernanceRequest:
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            project_id=payload.project_id,
            resource_type="governance_request",
            resource_id=organization_id,
        )
        if payload.project_id:
            project = self.db.get(Project, payload.project_id)
            if project is None or project.organization_id != organization_id:
                raise ValueError("Project not found.")
        request = GovernanceRequest(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            project_id=payload.project_id,
            request_type=payload.request_type,
            subject_type=payload.subject_type,
            subject_id_hash=(
                _reference_hash(payload.subject_id) if payload.subject_id else None
            ),
            requested_categories=sorted(
                set(payload.requested_categories or RESOURCE_CATEGORIES)
            ),
            status="PENDING_APPROVAL",
            evidence_preservation_policy=payload.evidence_preservation_policy,
            requested_by=actor,
        )
        self.db.add(request)
        self.db.commit()
        self.db.refresh(request)
        self._audit(request, actor, "governance.request.create", "Request created.")
        return request

    def approve(
        self,
        organization_id: str,
        request_id: str,
        actor: str,
        *,
        approved: bool,
        reason: str,
    ) -> GovernanceRequest:
        request = self._authorized(organization_id, request_id, actor)
        if request.status != "PENDING_APPROVAL":
            raise ValueError("Request is not awaiting approval.")
        if actor == request.requested_by:
            raise ValueError("Governance requests require independent approval.")
        request.approved_by = actor
        request.approval_reason = reason
        request.approved_at = _utcnow()
        request.status = "APPROVED" if approved else "CANCELLED"
        self.db.commit()
        self.db.refresh(request)
        self._audit(
            request,
            actor,
            "governance.request.approve",
            "Request approved." if approved else "Request rejected.",
        )
        return request

    def inventory(
        self, organization_id: str, request_id: str, actor: str
    ) -> GovernanceRequest:
        request = self._authorized(organization_id, request_id, actor)
        if request.status not in {"APPROVED", "INVENTORIED", "BLOCKED"}:
            raise ValueError("Request must be approved before inventory.")
        self.db.query(GovernanceRequestItem).filter(
            GovernanceRequestItem.request_id == request.id
        ).delete(synchronize_session=False)
        inventory: list[dict[str, Any]] = []
        for category in request.requested_categories:
            for resource_type, record in self._records(request, category):
                snapshot = self._snapshot(resource_type, record)
                inventory.append(
                    {
                        "category": category,
                        "resource_type": resource_type,
                        "resource_id": str(record.id),
                        "checksum": canonical_sha256(snapshot),
                        "snapshot": snapshot,
                    }
                )
        inventory.sort(
            key=lambda item: (
                item["category"],
                item["resource_type"],
                item["resource_id"],
            )
        )
        for position, item in enumerate(inventory, start=1):
            self.db.add(
                GovernanceRequestItem(
                    id=str(uuid.uuid4()),
                    request_id=request.id,
                    position=position,
                    resource_category=item["category"],
                    resource_type=item["resource_type"],
                    resource_id=item["resource_id"],
                    status="PENDING",
                    action=request.request_type,
                    checksum=item["checksum"],
                    export_metadata={"inventory": item["snapshot"]},
                )
            )
        request.dry_run_completed = True
        request.inventory_count = len(inventory)
        request.processed_count = 0
        request.blocked_count = 0
        request.processing_cursor = 0
        request.inventory_hash = canonical_sha256(
            [{key: value for key, value in item.items() if key != "snapshot"} for item in inventory]
        )
        request.status = "INVENTORIED"
        self.db.commit()
        self.db.refresh(request)
        self._audit(
            request,
            actor,
            "governance.request.inventory",
            "Dry-run inventory completed.",
            {"inventory_count": len(inventory), "inventory_hash": request.inventory_hash},
        )
        return request

    def process(
        self,
        organization_id: str,
        request_id: str,
        actor: str,
        *,
        batch_size: int = 100,
    ) -> GovernanceRequest:
        request = self._authorized(organization_id, request_id, actor)
        if not request.dry_run_completed or request.status not in {
            "INVENTORIED",
            "PROCESSING",
            "BLOCKED",
        }:
            raise ValueError("Dry-run inventory is required before processing.")
        request.status = "PROCESSING"
        items = (
            self.db.query(GovernanceRequestItem)
            .filter(
                GovernanceRequestItem.request_id == request.id,
                GovernanceRequestItem.status == "PENDING",
                GovernanceRequestItem.position > request.processing_cursor,
            )
            .order_by(GovernanceRequestItem.position)
            .limit(batch_size)
            .all()
        )
        for item in items:
            item.attempts += 1
            hold = self._active_hold(request, item)
            rule = self.residency.rule(
                request.organization_id,
                request.project_id,
                item.resource_category,
            )
            if request.request_type == "DELETION" and (
                hold is not None or (rule and rule.legal_hold)
            ):
                item.status = "BLOCKED_LEGAL_HOLD"
                item.legal_hold_id = hold.id if hold else None
                item.deletion_metadata = {
                    "schema_version": "governance-deletion/v1",
                    "outcome": "BLOCKED",
                    "reason": "legal_hold",
                    "policy_legal_hold": bool(rule and rule.legal_hold),
                }
                request.blocked_count += 1
            elif request.request_type == "EXPORT":
                if rule and not rule.exportable:
                    item.status = "PRESERVED"
                    item.export_metadata = {
                        **(item.export_metadata or {}),
                        "outcome": "NOT_EXPORTABLE",
                    }
                else:
                    item.status = "EXPORTED"
                    item.export_metadata = {
                        **(item.export_metadata or {}),
                        "outcome": "EXPORTED",
                        "checksum": item.checksum,
                    }
            else:
                self._apply_deletion(request, item, rule)
            item.processed_at = _utcnow()
            request.processing_cursor = item.position
            request.processed_count += 1
        remaining = (
            self.db.query(GovernanceRequestItem)
            .filter(
                GovernanceRequestItem.request_id == request.id,
                GovernanceRequestItem.status == "PENDING",
            )
            .count()
        )
        if remaining:
            request.status = "PROCESSING"
        elif request.blocked_count:
            request.status = "BLOCKED"
        else:
            request.status = "COMPLETED"
            request.completed_at = _utcnow()
        request.output_manifest = self._request_manifest(request)
        self.db.commit()
        self.db.refresh(request)
        self._audit(
            request,
            actor,
            "governance.request.process",
            "Governance request batch processed.",
            {
                "processed_count": request.processed_count,
                "blocked_count": request.blocked_count,
                "cursor": request.processing_cursor,
                "status": request.status,
            },
        )
        return request

    def list_requests(
        self, organization_id: str, actor: str
    ) -> list[GovernanceRequest]:
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            resource_type="governance_request",
            resource_id=organization_id,
        )
        return (
            self.db.query(GovernanceRequest)
            .filter(GovernanceRequest.organization_id == organization_id)
            .order_by(GovernanceRequest.created_at.desc())
            .all()
        )

    def get(
        self, organization_id: str, request_id: str, actor: str
    ) -> GovernanceRequest:
        return self._authorized(organization_id, request_id, actor)

    def create_hold(
        self, organization_id: str, payload: GovernanceLegalHoldCreate, actor: str
    ) -> GovernanceLegalHold:
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            project_id=payload.project_id,
            resource_type="governance_legal_hold",
            resource_id=organization_id,
        )
        hold = GovernanceLegalHold(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            project_id=payload.project_id,
            resource_category=payload.resource_category,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            subject_id_hash=(
                _reference_hash(payload.subject_id) if payload.subject_id else None
            ),
            reason=payload.reason,
            status="ACTIVE",
            created_by=actor,
        )
        self.db.add(hold)
        self.db.commit()
        self.db.refresh(hold)
        self.tenancy.audit_sensitive(
            organization_id,
            payload.project_id,
            actor,
            "governance.legal_hold.create",
            "governance_legal_hold",
            hold.id,
            "Legal hold created.",
        )
        return hold

    def release_hold(
        self, organization_id: str, hold_id: str, actor: str
    ) -> GovernanceLegalHold:
        hold = (
            self.db.query(GovernanceLegalHold)
            .filter(
                GovernanceLegalHold.id == hold_id,
                GovernanceLegalHold.organization_id == organization_id,
            )
            .first()
        )
        if hold is None:
            raise ValueError("Legal hold not found.")
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            project_id=hold.project_id,
            resource_type="governance_legal_hold",
            resource_id=hold.id,
        )
        hold.status = "RELEASED"
        hold.released_by = actor
        hold.released_at = _utcnow()
        self.db.commit()
        return hold

    def _authorized(
        self, organization_id: str, request_id: str, actor: str
    ) -> GovernanceRequest:
        request = (
            self.db.query(GovernanceRequest)
            .filter(
                GovernanceRequest.id == request_id,
                GovernanceRequest.organization_id == organization_id,
            )
            .first()
        )
        if request is None:
            raise ValueError("Governance request not found.")
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            project_id=request.project_id,
            resource_type="governance_request",
            resource_id=request.id,
        )
        return request

    def _records(
        self, request: GovernanceRequest, category: str
    ) -> list[tuple[str, Any]]:
        direct: dict[str, tuple[type[Any], str]] = {
            "task_metadata": (Task, "task"),
            "artifacts": (Artifact, "artifact"),
            "evidence": (EvidencePack, "evidence_pack"),
            "backups": (BackupRecord, "backup"),
            "analytics": (RunOutcomeAnalytics, "run_outcome_analytics"),
            "audit": (TenantAuditEvent, "tenant_audit_event"),
        }
        results: list[tuple[str, Any]] = []
        if category == "source_context":
            for model, name in (
                (ContextEvent, "context_event"),
                (MemoryChunk, "memory_chunk"),
            ):
                results.extend((name, item) for item in self._tenant_query(model, request))
        elif category == "runtime_events":
            runtime_query = self.db.query(RuntimeEvent).join(Run).filter(
                Run.organization_id == request.organization_id
            )
            if request.project_id:
                runtime_query = runtime_query.filter(
                    Run.project_id == request.project_id
                )
            results.extend(
                ("runtime_event", item) for item in runtime_query.all()
            )
        elif category == "logs":
            log_query = self.db.query(ContextEvent).filter(
                ContextEvent.organization_id == request.organization_id,
                ContextEvent.event_type.in_(["log", "agent_log", "runtime_log"]),
            )
            if request.project_id:
                log_query = log_query.filter(
                    ContextEvent.project_id == request.project_id
                )
            results.extend(("context_event", item) for item in log_query.all())
        elif category in direct:
            model, name = direct[category]
            results.extend((name, item) for item in self._tenant_query(model, request))
        if request.subject_type == "DATA_SUBJECT":
            results = [
                (name, item)
                for name, item in results
                if self._record_subject_hash(item) == request.subject_id_hash
            ]
        return results

    def _tenant_query(
        self, model: type[Any], request: GovernanceRequest
    ) -> list[Any]:
        query = self.db.query(model).filter(
            model.organization_id == request.organization_id
        )
        if request.project_id and hasattr(model, "project_id"):
            query = query.filter(model.project_id == request.project_id)
        return query.all()

    @staticmethod
    def _record_subject_hash(record: Any) -> str | None:
        for field in (
            getattr(record, "tenant_attribution", None),
            getattr(record, "metadata_", None),
            getattr(record, "request_metadata", None),
            getattr(record, "payload", None),
        ):
            if isinstance(field, dict):
                value = field.get("subject_id_hash")
                if isinstance(value, str):
                    return value
        return None

    @staticmethod
    def _snapshot(resource_type: str, record: Any) -> dict[str, Any]:
        snapshot = {
            "resource_type": resource_type,
            "resource_id": str(record.id),
            "created_at": getattr(record, "created_at", None),
            "classification": getattr(
                record,
                "storage_classification",
                getattr(record, "data_classification", None),
            ),
            "region": getattr(
                record, "storage_region", getattr(record, "data_region", None)
            ),
        }
        for name in (
            "status",
            "event_type",
            "artifact_type",
            "manifest_hash",
            "checksum",
            "sequence",
            "action",
        ):
            if hasattr(record, name):
                snapshot[name] = getattr(record, name)
        return json.loads(canonical_json(_sanitize(snapshot)))

    def _active_hold(
        self, request: GovernanceRequest, item: GovernanceRequestItem
    ) -> GovernanceLegalHold | None:
        query = self.db.query(GovernanceLegalHold).filter(
            GovernanceLegalHold.organization_id == request.organization_id,
            GovernanceLegalHold.status == "ACTIVE",
            or_(
                GovernanceLegalHold.project_id.is_(None),
                GovernanceLegalHold.project_id == request.project_id,
            ),
            or_(
                GovernanceLegalHold.resource_category.is_(None),
                GovernanceLegalHold.resource_category == item.resource_category,
            ),
            or_(
                GovernanceLegalHold.resource_type.is_(None),
                GovernanceLegalHold.resource_type == item.resource_type,
            ),
            or_(
                GovernanceLegalHold.resource_id.is_(None),
                GovernanceLegalHold.resource_id == item.resource_id,
            ),
            or_(
                GovernanceLegalHold.subject_id_hash.is_(None),
                GovernanceLegalHold.subject_id_hash == request.subject_id_hash,
            ),
        )
        return query.order_by(GovernanceLegalHold.created_at).first()

    @staticmethod
    def _apply_deletion(
        request: GovernanceRequest,
        item: GovernanceRequestItem,
        rule: DataGovernancePolicyRule | None,
    ) -> None:
        preservation = (
            rule.evidence_preservation if rule else request.evidence_preservation_policy
        )
        if item.resource_category == "evidence" and preservation == "PRESERVE":
            item.status = "PRESERVED"
            item.deletion_metadata = {
                "schema_version": "governance-deletion/v1",
                "outcome": "PRESERVED",
                "reason": "evidence_preservation_policy",
            }
            return
        mode = rule.deletion_mode if rule else "TOMBSTONE"
        now = _utcnow()
        metadata: dict[str, Any] = {
            "schema_version": "governance-deletion/v1",
            "mode": mode,
            "requested_by_request_id": request.id,
            "resource_checksum": item.checksum,
            "deleted_at": now.isoformat(),
            "tombstone_id": str(uuid.uuid4()),
            "recoverable": False,
        }
        if mode == "CRYPTOGRAPHIC":
            metadata["destroyed_key_reference_hash"] = hashlib.sha256(
                f"{request.id}:{item.resource_type}:{item.resource_id}".encode()
            ).hexdigest()
            metadata["key_destruction_attested"] = True
            item.status = "CRYPTOGRAPHICALLY_DELETED"
        elif mode == "HARD_DELETE":
            metadata["deletion_receipt"] = canonical_sha256(metadata)
            item.status = "DELETED"
        else:
            metadata["tombstone_checksum"] = canonical_sha256(metadata)
            item.status = "TOMBSTONED"
        item.deletion_metadata = metadata

    def _request_manifest(self, request: GovernanceRequest) -> dict[str, Any]:
        items = (
            self.db.query(GovernanceRequestItem)
            .filter(GovernanceRequestItem.request_id == request.id)
            .order_by(GovernanceRequestItem.position)
            .all()
        )
        manifest = {
            "schema_version": "governance-request-manifest/v1",
            "request_id": request.id,
            "organization_id": request.organization_id,
            "project_id": request.project_id,
            "request_type": request.request_type,
            "subject_type": request.subject_type,
            "subject_id_hash": request.subject_id_hash,
            "inventory_hash": request.inventory_hash,
            "evidence_preservation_policy": request.evidence_preservation_policy,
            "items": [
                {
                    "position": item.position,
                    "category": item.resource_category,
                    "resource_type": item.resource_type,
                    "resource_id": item.resource_id,
                    "status": item.status,
                    "checksum": item.checksum,
                    "deletion_metadata": item.deletion_metadata,
                    "export_metadata": item.export_metadata,
                }
                for item in items
            ],
        }
        return {**manifest, "manifest_checksum": canonical_sha256(manifest)}

    def _audit(
        self,
        request: GovernanceRequest,
        actor: str,
        action: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.tenancy.audit_sensitive(
            request.organization_id,
            request.project_id,
            actor,
            action,
            "governance_request",
            request.id,
            reason,
            metadata,
        )


class AuditExportService:
    def __init__(self, db: Session, private_key: Any | None = None) -> None:
        self.db = db
        self.private_key = private_key
        self.tenancy = TenancyService(db)

    def create(
        self, organization_id: str, payload: AuditExportCreate, actor: str
    ) -> AuditExportBatch:
        self.tenancy.require_permission(
            organization_id,
            actor,
            "audit.export",
            project_id=payload.project_id,
            resource_type="audit_export",
            resource_id=organization_id,
        )
        query = self.db.query(TenantAuditEvent).filter(
            TenantAuditEvent.organization_id == organization_id
        )
        if payload.project_id:
            query = query.filter(TenantAuditEvent.project_id == payload.project_id)
        if payload.start_sequence:
            query = query.filter(TenantAuditEvent.sequence >= payload.start_sequence)
        if payload.end_sequence:
            query = query.filter(TenantAuditEvent.sequence <= payload.end_sequence)
        events = query.order_by(TenantAuditEvent.sequence).limit(payload.limit).all()
        if not events:
            raise ValueError("No audit events matched the export range.")
        event_documents = [self._event_document(event) for event in events]
        manifest = {
            "schema_version": "audit-export/v1",
            "organization_id": organization_id,
            "project_id": payload.project_id,
            "start_sequence": events[0].sequence,
            "end_sequence": events[-1].sequence,
            "event_count": len(events),
            "scope_contiguous": payload.project_id is None,
            "chain_root_hash": events[0].previous_event_hash,
            "chain_end_hash": events[-1].event_hash,
            "events": event_documents,
        }
        checksum = canonical_sha256(manifest)
        private_key = self._private_key()
        signature = private_key.sign(canonical_json(manifest))
        public_key = private_key.public_key()
        public_pem, fingerprint = self._public_key_metadata(public_key)
        batch = AuditExportBatch(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            project_id=payload.project_id,
            start_sequence=events[0].sequence,
            end_sequence=events[-1].sequence,
            event_count=len(events),
            chain_root_hash=events[0].previous_event_hash,
            chain_end_hash=events[-1].event_hash,
            canonical_manifest=manifest,
            manifest_checksum=checksum,
            signature_key_id=os.getenv("SACM_AUDIT_EXPORT_SIGNING_KEY_ID", "audit-export-v1"),
            public_key=public_pem,
            public_key_fingerprint=fingerprint,
            signature=base64.b64encode(signature).decode(),
            created_by=actor,
        )
        self.db.add(batch)
        self.db.commit()
        self.db.refresh(batch)
        self.tenancy.audit_sensitive(
            organization_id,
            payload.project_id,
            actor,
            "audit.export.create",
            "audit_export",
            batch.id,
            "Immutable signed audit export created.",
            {
                "start_sequence": batch.start_sequence,
                "end_sequence": batch.end_sequence,
                "manifest_checksum": batch.manifest_checksum,
            },
        )
        return batch

    def get(
        self, organization_id: str, batch_id: str, actor: str
    ) -> AuditExportBatch:
        batch = (
            self.db.query(AuditExportBatch)
            .filter(
                AuditExportBatch.id == batch_id,
                AuditExportBatch.organization_id == organization_id,
            )
            .first()
        )
        if batch is None:
            raise ValueError("Audit export not found.")
        self.tenancy.require_permission(
            organization_id,
            actor,
            "audit.export",
            project_id=batch.project_id,
            resource_type="audit_export",
            resource_id=batch.id,
        )
        return batch

    def list(
        self, organization_id: str, actor: str
    ) -> list[AuditExportBatch]:
        self.tenancy.require_permission(
            organization_id,
            actor,
            "audit.export",
            resource_type="audit_export",
            resource_id=organization_id,
        )
        return (
            self.db.query(AuditExportBatch)
            .filter(AuditExportBatch.organization_id == organization_id)
            .order_by(AuditExportBatch.created_at.desc())
            .all()
        )

    @classmethod
    def verify(cls, batch: AuditExportBatch) -> dict[str, Any]:
        errors: list[str] = []
        manifest = batch.canonical_manifest
        if canonical_sha256(manifest) != batch.manifest_checksum:
            errors.append("Manifest checksum mismatch.")
        events = manifest.get("events", []) if isinstance(manifest, dict) else []
        previous = manifest.get("chain_root_hash") if isinstance(manifest, dict) else None
        previous_sequence: int | None = None
        for event in events:
            sequence = event.get("sequence")
            consecutive = (
                previous_sequence is None
                or isinstance(sequence, int)
                and sequence == previous_sequence + 1
            )
            if consecutive and event.get("previous_event_hash") != previous:
                errors.append("Audit event chain link mismatch.")
                break
            if canonical_sha256(event.get("event")) != event.get("event_checksum"):
                errors.append("Audit event checksum mismatch.")
                break
            if event.get("event_hash") != event.get("event", {}).get("event_hash"):
                errors.append("Audit event hash metadata mismatch.")
                break
            document = event.get("event", {})
            canonical_event = {
                key: document.get(key)
                for key in (
                    "organization_id",
                    "project_id",
                    "sequence",
                    "actor_id",
                    "actor_type",
                    "service_credential_id",
                    "action",
                    "resource_type",
                    "resource_id",
                    "decision",
                    "reason",
                    "correlation_id",
                    "request_metadata",
                    "previous_event_hash",
                    "created_at",
                )
            }
            if canonical_sha256(canonical_event) != event.get("event_hash"):
                errors.append("Audit event hash is invalid.")
                break
            previous = event.get("event_hash")
            previous_sequence = sequence if isinstance(sequence, int) else None
        if events and previous != manifest.get("chain_end_hash"):
            errors.append("Audit chain end hash mismatch.")
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )

            public_key = serialization.load_pem_public_key(batch.public_key.encode())
            if not isinstance(public_key, Ed25519PublicKey):
                raise ValueError("Audit export public key is not Ed25519.")
            public_key.verify(
                base64.b64decode(batch.signature), canonical_json(manifest)
            )
        except InvalidSignature:
            errors.append("Audit export signature is invalid.")
        except (TypeError, ValueError):
            errors.append("Audit export signature metadata is invalid.")
        return {"valid": not errors, "errors": errors}

    @staticmethod
    def _event_document(event: TenantAuditEvent) -> dict[str, Any]:
        document = {
            "id": event.id,
            "organization_id": event.organization_id,
            "project_id": event.project_id,
            "sequence": event.sequence,
            "actor_id": event.actor_id,
            "actor_type": event.actor_type,
            "service_credential_id": event.service_credential_id,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "decision": event.decision,
            "reason": event.reason,
            "correlation_id": event.correlation_id,
            "request_metadata": _sanitize(event.request_metadata),
            "previous_event_hash": event.previous_event_hash,
            "event_hash": event.event_hash,
            "created_at": event.created_at.isoformat(timespec="microseconds"),
        }
        return {
            "sequence": event.sequence,
            "previous_event_hash": event.previous_event_hash,
            "event_hash": event.event_hash,
            "event_checksum": canonical_sha256(document),
            "event": document,
        }

    def _private_key(self) -> Any:
        if self.private_key is not None:
            return self.private_key
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
        except ImportError as exc:
            raise RuntimeError("Ed25519 audit export signing requires cryptography.") from exc
        value = os.getenv("SACM_AUDIT_EXPORT_SIGNING_PRIVATE_KEY")
        file_path = os.getenv("SACM_AUDIT_EXPORT_SIGNING_PRIVATE_KEY_FILE")
        if file_path:
            value = Path(file_path).read_text(encoding="utf-8")
        if value:
            key = serialization.load_pem_private_key(value.encode(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("Audit export signing key must be Ed25519.")
            return key
        if production_mode():
            raise ValueError("Production audit exports require a configured signing key.")
        return Ed25519PrivateKey.generate()

    @staticmethod
    def _public_key_metadata(public_key: Any) -> tuple[str, str]:
        from cryptography.hazmat.primitives import serialization

        pem = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return pem, hashlib.sha256(der).hexdigest()


class SIEMTransport(Protocol):
    def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
        timeout: float,
    ) -> Any: ...


SecretResolver = Callable[[str], str | None]
AddressResolver = Callable[[str], list[str]]


def _default_secret_resolver(reference_hash: str) -> str | None:
    prefix = reference_hash[:16].upper()
    file_path = os.getenv(f"SACM_SIEM_SECRET_{prefix}_FILE")
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return os.getenv(f"SACM_SIEM_SECRET_{prefix}")


def _default_address_resolver(host: str) -> list[str]:
    return sorted(
        {
            str(item[4][0])
            for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        }
    )


class SafeHTTPAdapter:
    def __init__(
        self,
        transport: SIEMTransport | None = None,
        address_resolver: AddressResolver = _default_address_resolver,
    ) -> None:
        self.transport = transport or self._send
        self.address_resolver = address_resolver

    def deliver(
        self,
        sink: SIEMSink,
        payload: bytes,
        *,
        signature: str,
        idempotency_key: str,
        authorization: str | None,
    ) -> dict[str, Any]:
        endpoint = validate_http_endpoint(
            sink.endpoint or "", sink.allowed_hosts, resolve=False
        )
        host = urlsplit(endpoint).hostname or ""
        for address in self.address_resolver(host):
            if _unsafe_address(address):
                raise ValueError("SIEM endpoint resolves to a disallowed network.")
        headers = {
            "Content-Type": "application/json",
            "X-SACM-Signature": f"hmac-sha256={signature}",
            "Idempotency-Key": idempotency_key,
        }
        if authorization:
            headers["Authorization"] = f"Bearer {authorization}"
        response = self.transport(
            endpoint, headers=headers, content=payload, timeout=10.0
        )
        status_code = int(getattr(response, "status_code", 0))
        if status_code < 200 or status_code >= 300:
            raise RuntimeError(f"http_status_{status_code}")
        return {"status_code": status_code}

    @staticmethod
    def _send(
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
        timeout: float,
    ) -> httpx.Response:
        return httpx.post(
            url,
            headers=headers,
            content=content,
            timeout=timeout,
            follow_redirects=False,
        )


class MetadataSIEMAdapter:
    def deliver(
        self,
        sink: SIEMSink,
        payload: bytes,
        *,
        signature: str,
        idempotency_key: str,
        authorization: str | None,
    ) -> dict[str, Any]:
        del authorization
        if sink.sink_type == "SYSLOG":
            required = {"host", "port", "protocol"}
            if not required <= set(sink.storage_metadata):
                raise ValueError("Syslog sink requires host, port, and protocol metadata.")
        elif sink.sink_type in {"FILE", "OBJECT_STORAGE"}:
            if not sink.storage_metadata.get("uri"):
                raise ValueError("Storage sink requires credential-free uri metadata.")
        return {
            "contract": sink.sink_type,
            "payload_checksum": hashlib.sha256(payload).hexdigest(),
            "signature": signature,
            "idempotency_key": idempotency_key,
        }


def _unsafe_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_http_endpoint(
    endpoint: str, allowed_hosts: list[str], *, resolve: bool = False
) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("SIEM HTTP endpoint is invalid or contains unsafe components.")
    host = parsed.hostname.lower().rstrip(".")
    normalized_allowlist = {item.lower().rstrip(".") for item in allowed_hosts}
    if not normalized_allowlist or host not in normalized_allowlist:
        raise ValueError("SIEM HTTP endpoint host is not allowlisted.")
    if production_mode() and parsed.scheme != "https":
        raise ValueError("Production SIEM HTTP endpoints must use HTTPS.")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("SIEM HTTP endpoint targets a disallowed network.")
    try:
        if _unsafe_address(host):
            raise ValueError("SIEM HTTP endpoint targets a disallowed network.")
    except ValueError as exc:
        if "does not appear to be" not in str(exc):
            raise
    if resolve:
        for address in _default_address_resolver(host):
            if _unsafe_address(address):
                raise ValueError("SIEM endpoint resolves to a disallowed network.")
    return endpoint


class SIEMService:
    def __init__(
        self,
        db: Session,
        *,
        http_adapter: SafeHTTPAdapter | None = None,
        metadata_adapter: MetadataSIEMAdapter | None = None,
        secret_resolver: SecretResolver = _default_secret_resolver,
    ) -> None:
        self.db = db
        self.http_adapter = http_adapter or SafeHTTPAdapter()
        self.metadata_adapter = metadata_adapter or MetadataSIEMAdapter()
        self.secret_resolver = secret_resolver
        self.tenancy = TenancyService(db)

    def create(
        self, organization_id: str, payload: SIEMSinkCreate, actor: str
    ) -> SIEMSink:
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            project_id=payload.project_id,
            resource_type="siem_sink",
            resource_id=payload.name,
        )
        if payload.project_id:
            project = self.db.get(Project, payload.project_id)
            if project is None or project.organization_id != organization_id:
                raise ValueError("Project not found.")
        if payload.sink_type == "HTTP_WEBHOOK":
            validate_http_endpoint(
                payload.endpoint or "", payload.allowed_hosts, resolve=False
            )
        elif payload.endpoint:
            raise ValueError("Only HTTP webhook sinks accept endpoint.")
        if not payload.signing_reference:
            raise ValueError("SIEM sinks require a signing reference.")
        safe_storage = self._validate_storage_metadata(
            payload.sink_type, payload.storage_metadata
        )
        if canonical_json(safe_storage) != canonical_json(payload.storage_metadata):
            raise ValueError("SIEM storage metadata must not contain secrets.")
        sink = SIEMSink(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            project_id=payload.project_id,
            name=payload.name,
            sink_type=payload.sink_type,
            status="ACTIVE",
            endpoint=payload.endpoint,
            allowed_hosts=sorted(set(payload.allowed_hosts)),
            storage_metadata=safe_storage,
            credential_reference_hash=(
                _reference_hash(payload.credential_reference)
                if payload.credential_reference
                else None
            ),
            credential_reference_hint=(
                "configured" if payload.credential_reference else None
            ),
            signing_reference_hash=_reference_hash(payload.signing_reference),
            batch_size=payload.batch_size,
            max_attempts=payload.max_attempts,
            backoff_seconds=payload.backoff_seconds,
            created_by=actor,
        )
        self.db.add(sink)
        self.db.commit()
        self.db.refresh(sink)
        self._audit(sink, actor, "siem.sink.create", "SIEM sink created.")
        return sink

    def update(
        self,
        organization_id: str,
        sink_id: str,
        payload: SIEMSinkUpdate,
        actor: str,
    ) -> SIEMSink:
        sink = self._authorized(organization_id, sink_id, actor, "data.manage")
        values = payload.model_dump(exclude_unset=True)
        credential_reference = values.pop("credential_reference", None)
        signing_reference = values.pop("signing_reference", None)
        if "storage_metadata" in values:
            values["storage_metadata"] = self._validate_storage_metadata(
                sink.sink_type, values["storage_metadata"]
            )
        endpoint = values.get("endpoint", sink.endpoint)
        allowed_hosts = values.get("allowed_hosts", sink.allowed_hosts)
        if sink.sink_type == "HTTP_WEBHOOK":
            validate_http_endpoint(endpoint or "", allowed_hosts, resolve=False)
        for key, value in values.items():
            setattr(sink, key, value)
        if credential_reference:
            sink.credential_reference_hash = _reference_hash(credential_reference)
            sink.credential_reference_hint = "configured"
        if signing_reference:
            sink.signing_reference_hash = _reference_hash(signing_reference)
        self.db.commit()
        self.db.refresh(sink)
        self._audit(sink, actor, "siem.sink.update", "SIEM sink updated.")
        return sink

    def list_sinks(self, organization_id: str, actor: str) -> list[SIEMSink]:
        self.tenancy.require_permission(
            organization_id,
            actor,
            "data.manage",
            resource_type="siem_sink",
            resource_id=organization_id,
        )
        return (
            self.db.query(SIEMSink)
            .filter(SIEMSink.organization_id == organization_id)
            .order_by(SIEMSink.created_at)
            .all()
        )

    def drain(
        self,
        organization_id: str,
        sink_id: str,
        actor: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        sink = self._authorized(organization_id, sink_id, actor, "data.manage")
        if sink.status != "ACTIVE":
            raise ValueError("SIEM sink is not active.")
        delivered = retried = dead_lettered = 0
        now = _utcnow()
        due = (
            self.db.query(SIEMDelivery)
            .filter(
                SIEMDelivery.sink_id == sink.id,
                SIEMDelivery.status.in_(["PENDING", "RETRY"]),
                or_(
                    SIEMDelivery.next_attempt_at.is_(None),
                    SIEMDelivery.next_attempt_at <= now,
                ),
            )
            .order_by(SIEMDelivery.created_at)
            .limit(limit)
            .all()
        )
        if not due:
            self._create_delivery(sink)
            due = (
                self.db.query(SIEMDelivery)
                .filter(
                    SIEMDelivery.sink_id == sink.id,
                    SIEMDelivery.status == "PENDING",
                )
                .order_by(SIEMDelivery.created_at)
                .limit(limit)
                .all()
            )
        for delivery in due:
            outcome = self._deliver(sink, delivery)
            if outcome == "DELIVERED":
                delivered += 1
            elif outcome == "RETRY":
                retried += 1
            else:
                dead_lettered += 1
        return {
            "sink_id": sink.id,
            "delivered": delivered,
            "retried": retried,
            "dead_lettered": dead_lettered,
            "cursor_sequence": sink.cursor_sequence,
        }

    def deliveries(
        self, organization_id: str, sink_id: str, actor: str
    ) -> list[SIEMDelivery]:
        sink = self._authorized(organization_id, sink_id, actor, "data.manage")
        return (
            self.db.query(SIEMDelivery)
            .filter(SIEMDelivery.sink_id == sink.id)
            .order_by(SIEMDelivery.created_at.desc())
            .all()
        )

    def retry_dead_letter(
        self, organization_id: str, delivery_id: str, actor: str
    ) -> SIEMDelivery:
        delivery = (
            self.db.query(SIEMDelivery)
            .filter(
                SIEMDelivery.id == delivery_id,
                SIEMDelivery.organization_id == organization_id,
            )
            .first()
        )
        if delivery is None:
            raise ValueError("SIEM delivery not found.")
        sink = self._authorized(
            organization_id, delivery.sink_id, actor, "data.manage"
        )
        if delivery.status != "DEAD_LETTER":
            raise ValueError("Only dead-letter deliveries can be retried.")
        delivery.status = "RETRY"
        delivery.attempts = 0
        delivery.next_attempt_at = _utcnow()
        delivery.error_code = None
        sink.last_error_code = None
        self.db.commit()
        return delivery

    @staticmethod
    def mark_pending(db: Session, event: TenantAuditEvent) -> None:
        sinks = (
            db.query(SIEMSink)
            .filter(
                SIEMSink.organization_id == event.organization_id,
                SIEMSink.status == "ACTIVE",
            )
            .all()
        )
        changed = False
        for sink in sinks:
            metadata = dict(sink.storage_metadata or {})
            pending = int(metadata.get("pending_through_sequence", 0))
            if event.sequence > pending:
                metadata["pending_through_sequence"] = event.sequence
                sink.storage_metadata = metadata
                changed = True
        if changed:
            db.commit()

    def _create_delivery(self, sink: SIEMSink) -> SIEMDelivery | None:
        events_query = self.db.query(TenantAuditEvent).filter(
            TenantAuditEvent.organization_id == sink.organization_id,
            TenantAuditEvent.sequence > sink.cursor_sequence,
        )
        if sink.project_id:
            events_query = events_query.filter(
                TenantAuditEvent.project_id == sink.project_id
            )
        events = (
            events_query.order_by(TenantAuditEvent.sequence)
            .limit(sink.batch_size)
            .all()
        )
        if not events:
            return None
        payload = self._payload(sink, events)
        payload_bytes = canonical_json(payload)
        signing_secret = self._secret(sink.signing_reference_hash)
        signature = hmac.new(
            signing_secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        idempotency_key = canonical_sha256(
            {
                "sink_id": sink.id,
                "first_sequence": events[0].sequence,
                "last_sequence": events[-1].sequence,
                "payload_checksum": hashlib.sha256(payload_bytes).hexdigest(),
            }
        )
        existing = (
            self.db.query(SIEMDelivery)
            .filter(
                SIEMDelivery.sink_id == sink.id,
                SIEMDelivery.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing:
            return existing
        delivery = SIEMDelivery(
            id=str(uuid.uuid4()),
            sink_id=sink.id,
            organization_id=sink.organization_id,
            first_sequence=events[0].sequence,
            last_sequence=events[-1].sequence,
            event_count=len(events),
            idempotency_key=idempotency_key,
            payload_checksum=hashlib.sha256(payload_bytes).hexdigest(),
            payload_signature=signature,
            payload_metadata={
                "schema_version": "siem-delivery/v1",
                "event_sequences": [event.sequence for event in events],
            },
            status="PENDING",
            next_attempt_at=_utcnow(),
        )
        self.db.add(delivery)
        self.db.commit()
        return delivery

    def _deliver(self, sink: SIEMSink, delivery: SIEMDelivery) -> str:
        events = (
            self.db.query(TenantAuditEvent)
            .filter(
                TenantAuditEvent.organization_id == sink.organization_id,
                TenantAuditEvent.sequence >= delivery.first_sequence,
                TenantAuditEvent.sequence <= delivery.last_sequence,
            )
            .order_by(TenantAuditEvent.sequence)
            .all()
        )
        payload_bytes = canonical_json(self._payload(sink, events))
        if hashlib.sha256(payload_bytes).hexdigest() != delivery.payload_checksum:
            delivery.status = "DEAD_LETTER"
            delivery.error_code = "payload_checksum_mismatch"
            self.db.commit()
            return delivery.status
        adapter = (
            self.http_adapter
            if sink.sink_type == "HTTP_WEBHOOK"
            else self.metadata_adapter
        )
        delivery.attempts += 1
        try:
            receipt = adapter.deliver(
                sink,
                payload_bytes,
                signature=delivery.payload_signature,
                idempotency_key=delivery.idempotency_key,
                authorization=(
                    self._secret(sink.credential_reference_hash)
                    if sink.credential_reference_hash
                    else None
                ),
            )
            delivery.status = "DELIVERED"
            delivery.delivered_at = _utcnow()
            delivery.next_attempt_at = None
            delivery.response_metadata = _sanitize(receipt)
            delivery.error_code = None
            sink.cursor_sequence = max(
                sink.cursor_sequence, delivery.last_sequence
            )
            sink.last_success_at = delivery.delivered_at
            sink.last_error_code = None
        except Exception as exc:
            delivery.error_code = self._safe_error_code(exc)
            sink.last_failure_at = _utcnow()
            sink.last_error_code = delivery.error_code
            if delivery.attempts >= sink.max_attempts:
                delivery.status = "DEAD_LETTER"
                delivery.next_attempt_at = None
            else:
                delivery.status = "RETRY"
                delay = sink.backoff_seconds * (2 ** (delivery.attempts - 1))
                delivery.next_attempt_at = _utcnow() + timedelta(seconds=delay)
        self.db.commit()
        return delivery.status

    @staticmethod
    def _payload(sink: SIEMSink, events: list[TenantAuditEvent]) -> dict[str, Any]:
        return {
            "schema_version": "siem-audit-batch/v1",
            "sink_id": sink.id,
            "organization_id": sink.organization_id,
            "project_id": sink.project_id,
            "events": [AuditExportService._event_document(event)["event"] for event in events],
        }

    def _secret(self, reference_hash: str | None) -> str:
        if not reference_hash:
            raise ValueError("SIEM secret reference is not configured.")
        value = self.secret_resolver(reference_hash)
        if not value:
            raise ValueError("SIEM secret reference could not be resolved.")
        return value

    def _authorized(
        self,
        organization_id: str,
        sink_id: str,
        actor: str,
        permission: str,
    ) -> SIEMSink:
        sink = (
            self.db.query(SIEMSink)
            .filter(
                SIEMSink.id == sink_id,
                SIEMSink.organization_id == organization_id,
            )
            .first()
        )
        if sink is None:
            raise ValueError("SIEM sink not found.")
        self.tenancy.require_permission(
            organization_id,
            actor,
            permission,
            project_id=sink.project_id,
            resource_type="siem_sink",
            resource_id=sink.id,
        )
        return sink

    def _audit(self, sink: SIEMSink, actor: str, action: str, reason: str) -> None:
        self.tenancy.audit_sensitive(
            sink.organization_id,
            sink.project_id,
            actor,
            action,
            "siem_sink",
            sink.id,
            reason,
            {
                "sink_type": sink.sink_type,
                "status": sink.status,
                "credential_reference_configured": bool(
                    sink.credential_reference_hash
                ),
                "signing_reference_configured": bool(sink.signing_reference_hash),
            },
        )

    @staticmethod
    def _safe_error_code(exc: Exception) -> str:
        message = str(exc)
        if message.startswith("http_status_"):
            return message
        return type(exc).__name__

    @staticmethod
    def _validate_storage_metadata(
        sink_type: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        allowed = {
            "HTTP_WEBHOOK": {"content_type"},
            "SYSLOG": {"host", "port", "protocol", "facility"},
            "FILE": {"uri", "region", "storage_class"},
            "OBJECT_STORAGE": {
                "uri",
                "region",
                "storage_class",
                "bucket",
                "prefix",
            },
        }[sink_type]
        unknown = set(metadata).difference(allowed)
        if unknown:
            raise ValueError("SIEM storage metadata contains unsupported fields.")
        safe = _sanitize(metadata)
        if canonical_json(safe) != canonical_json(metadata):
            raise ValueError("SIEM storage metadata must not contain secrets.")
        uri = safe.get("uri")
        if isinstance(uri, str):
            parsed = urlsplit(uri)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("SIEM storage URI must not contain credentials.")
        return safe


def governance_health(db: Session, organization_id: str | None) -> dict[str, Any]:
    request_query = db.query(GovernanceRequest).filter(
        GovernanceRequest.status.in_(
            ["PENDING_APPROVAL", "APPROVED", "INVENTORIED", "PROCESSING", "BLOCKED"]
        )
    )
    delivery_query = db.query(SIEMDelivery).filter(
        SIEMDelivery.status.in_(["PENDING", "RETRY", "DEAD_LETTER"])
    )
    sink_query = db.query(SIEMSink).filter(SIEMSink.status == "ACTIVE")
    if organization_id:
        request_query = request_query.filter(
            GovernanceRequest.organization_id == organization_id
        )
        delivery_query = delivery_query.filter(
            SIEMDelivery.organization_id == organization_id
        )
        sink_query = sink_query.filter(SIEMSink.organization_id == organization_id)
    backlog = request_query.count()
    dead_letters = delivery_query.filter(
        SIEMDelivery.status == "DEAD_LETTER"
    ).count()
    pending_deliveries = delivery_query.filter(
        SIEMDelivery.status.in_(["PENDING", "RETRY"])
    ).count()
    active_sinks = sink_query.count()
    status = (
        "UNHEALTHY"
        if dead_letters
        else "DEGRADED"
        if backlog or pending_deliveries
        else "HEALTHY"
    )
    return {
        "status": status,
        "governance_backlog": backlog,
        "active_sinks": active_sinks,
        "pending_deliveries": pending_deliveries,
        "dead_letter_deliveries": dead_letters,
    }
