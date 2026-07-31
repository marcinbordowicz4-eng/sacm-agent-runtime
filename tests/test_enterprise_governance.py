from datetime import datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.api.routes.governance import _sink
from sacm.core.governance_service import (
    AuditExportService,
    GovernancePolicyService,
    GovernanceRequestService,
    ResidencyService,
    SafeHTTPAdapter,
    SIEMService,
    validate_http_endpoint,
)
from sacm.core.tenancy_service import TenancyService
from sacm.infrastructure.db.models import GovernanceRequestItem, SIEMDelivery, Task
from sacm.schemas.governance import (
    AuditExportCreate,
    GovernanceLegalHoldCreate,
    GovernancePolicyCreate,
    GovernanceRequestCreate,
    GovernanceRuleInput,
    SIEMSinkCreate,
)


def _organization(db, slug="governance"):
    tenancy = TenancyService(db)
    organization = tenancy.create_organization(slug, slug.title(), "owner")
    tenancy.add_member(organization.id, "owner", "approver", "admin")
    project = tenancy.create_project(
        organization.id, f"{slug}-project", "Project", "owner"
    )
    return organization, project


def _policy_payload(project_id=None, *, legal_hold=False):
    return GovernancePolicyCreate(
        project_id=project_id,
        name="Enterprise governance",
        rules=[
            GovernanceRuleInput(
                resource_category=category,
                classification=(
                    "Restricted" if category in {"evidence", "audit"} else "Confidential"
                ),
                retention_days=365,
                legal_hold=legal_hold and category == "task_metadata",
                deletion_mode=(
                    "CRYPTOGRAPHIC" if category == "task_metadata" else "TOMBSTONE"
                ),
                exportable=True,
                allowed_regions=["eu-west-1"],
                storage_classes=["encrypted-standard"],
                evidence_preservation="PRESERVE",
            )
            for category in (
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
        ],
    )


def _active_policy(db, organization_id, project_id=None, *, legal_hold=False):
    service = GovernancePolicyService(db)
    policy = service.create(
        organization_id,
        _policy_payload(project_id, legal_hold=legal_hold),
        "owner",
    )
    return service.activate(organization_id, policy.id, "owner")


def test_policy_residency_rejects_disallowed_production_region(db, monkeypatch):
    organization, project = _organization(db)
    _active_policy(db, organization.id, project.id)
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")

    with pytest.raises(ValueError, match="not allowed"):
        ResidencyService(db).resolve(
            organization_id=organization.id,
            project_id=project.id,
            category="evidence",
            region="us-east-1",
        )

    metadata = ResidencyService(db).resolve(
        organization_id=organization.id,
        project_id=project.id,
        category="evidence",
    )
    assert metadata == {
        "region": "eu-west-1",
        "classification": "Restricted",
        "storage_class": "encrypted-standard",
    }


def test_deletion_requires_dry_run_resumes_and_records_crypto_tombstones(db):
    organization, project = _organization(db, "deletion")
    _active_policy(db, organization.id, project.id)
    for index in range(2):
        db.add(
            Task(
                id=f"task-{index}",
                organization_id=organization.id,
                project_id=project.id,
                title=f"Task {index}",
                description="delete me",
            )
        )
    db.commit()
    service = GovernanceRequestService(db)
    request = service.create(
        organization.id,
        GovernanceRequestCreate(
            project_id=project.id,
            request_type="DELETION",
            requested_categories=["task_metadata"],
        ),
        "owner",
    )
    service.approve(
        organization.id,
        request.id,
        "approver",
        approved=True,
        reason="Verified request",
    )

    with pytest.raises(ValueError, match="Dry-run"):
        service.process(organization.id, request.id, "owner", batch_size=1)

    request = service.inventory(organization.id, request.id, "owner")
    assert request.inventory_count == 2
    request = service.process(organization.id, request.id, "owner", batch_size=1)
    assert request.status == "PROCESSING"
    assert request.processing_cursor == 1
    request = service.process(organization.id, request.id, "owner", batch_size=1)
    assert request.status == "COMPLETED"
    items = (
        db.query(GovernanceRequestItem)
        .filter(GovernanceRequestItem.request_id == request.id)
        .all()
    )
    assert {item.status for item in items} == {"CRYPTOGRAPHICALLY_DELETED"}
    assert all(
        item.deletion_metadata["destroyed_key_reference_hash"] for item in items
    )


def test_legal_hold_blocks_deletion_and_evidence_is_explicitly_preserved(db):
    organization, project = _organization(db, "legal-hold")
    _active_policy(db, organization.id, project.id, legal_hold=True)
    db.add(
        Task(
            id="held-task",
            organization_id=organization.id,
            project_id=project.id,
            title="Held",
            description="must remain",
        )
    )
    db.commit()
    service = GovernanceRequestService(db)
    hold = service.create_hold(
        organization.id,
        GovernanceLegalHoldCreate(
            project_id=project.id,
            resource_category="task_metadata",
            reason="Litigation",
        ),
        "owner",
    )
    request = service.create(
        organization.id,
        GovernanceRequestCreate(
            project_id=project.id,
            request_type="DELETION",
            requested_categories=["task_metadata"],
        ),
        "owner",
    )
    service.approve(
        organization.id,
        request.id,
        "approver",
        approved=True,
        reason="Approved",
    )
    service.inventory(organization.id, request.id, "owner")
    request = service.process(organization.id, request.id, "owner")

    assert request.status == "BLOCKED"
    item = db.query(GovernanceRequestItem).filter_by(request_id=request.id).one()
    assert item.status == "BLOCKED_LEGAL_HOLD"
    assert item.legal_hold_id == hold.id


def test_audit_exports_are_tenant_scoped_signed_and_detect_tampering(db):
    organization, _ = _organization(db, "audit")
    other, _ = _organization(db, "other")
    key = Ed25519PrivateKey.generate()
    service = AuditExportService(db, key)
    batch = service.create(organization.id, AuditExportCreate(limit=100), "owner")

    assert AuditExportService.verify(batch) == {"valid": True, "errors": []}
    with pytest.raises(ValueError, match="not found"):
        service.get(other.id, batch.id, "owner")

    batch.canonical_manifest["events"][0]["event"]["reason"] = "tampered"
    result = AuditExportService.verify(batch)
    assert result["valid"] is False
    assert result["errors"]


class _Response:
    status_code = 200


class _FlakyTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, url, *, headers, content, timeout):
        self.calls.append((url, headers, content, timeout))
        if len(self.calls) == 1:
            raise RuntimeError("contains super-secret-value")
        return _Response()


@pytest.mark.security_release_gate
def test_siem_ssrf_secrets_retries_and_idempotency(db, request):
    request.node.user_properties.append(("security_test_id", "SRG-ADV-006"))
    organization, _ = _organization(db, "siem")
    with pytest.raises(ValueError, match="disallowed"):
        validate_http_endpoint(
            "http://127.0.0.1/audit", ["127.0.0.1"], resolve=False
        )
    transport = _FlakyTransport()
    service = SIEMService(
        db,
        http_adapter=SafeHTTPAdapter(
            transport=transport, address_resolver=lambda _: ["93.184.216.34"]
        ),
        secret_resolver=lambda _: "super-secret-value",
    )
    sink = service.create(
        organization.id,
        SIEMSinkCreate(
            name="soc",
            sink_type="HTTP_WEBHOOK",
            endpoint="https://siem.example.test/audit",
            allowed_hosts=["siem.example.test"],
            credential_reference="vault://siem/bearer",
            signing_reference="vault://siem/signing",
            batch_size=100,
            max_attempts=2,
            backoff_seconds=1,
        ),
        "owner",
    )
    serialized = str(_sink(sink))
    assert "vault://siem" not in serialized
    assert "super-secret-value" not in serialized

    first = service.drain(organization.id, sink.id, "owner")
    assert first["retried"] == 1
    delivery = db.query(SIEMDelivery).filter_by(sink_id=sink.id).one()
    assert delivery.error_code == "RuntimeError"
    assert "super-secret-value" not in str(delivery.response_metadata)
    delivery.next_attempt_at = datetime.min
    db.commit()

    second = service.drain(organization.id, sink.id, "owner")
    assert second["delivered"] == 1
    assert transport.calls[0][1]["Idempotency-Key"] == transport.calls[1][1][
        "Idempotency-Key"
    ]
    assert db.query(SIEMDelivery).filter_by(sink_id=sink.id).count() == 1
