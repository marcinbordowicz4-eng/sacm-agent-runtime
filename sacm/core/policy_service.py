import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import Approval, Run


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    requires_approval: bool = False


class ApprovalRequiredError(PermissionError):
    def __init__(self, approval: Approval) -> None:
        self.approval = approval
        super().__init__(f"Action requires approval: {approval.id}")


class PolicyService:
    """Evaluates tool actions locally or through OPA without permissive fallbacks."""

    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    def evaluate(
        self,
        action: str,
        resource: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> PolicyDecision:
        payload = {"action": action, "resource": resource, "run_id": run_id}
        opa_url = os.getenv("SACM_OPA_URL")
        if opa_url:
            return self._evaluate_opa(opa_url, payload)
        return self._evaluate_local(action)

    def require_allowed(
        self,
        action: str,
        resource: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> None:
        decision = self.evaluate(action, resource, run_id=run_id)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        if decision.requires_approval:
            if not run_id or self.db is None:
                raise PermissionError(
                    f"{action} requires a durable run and an approved request."
                )
            approval = self._pending_or_approved(run_id, action, resource)
            if approval.status != "APPROVED":
                raise ApprovalRequiredError(approval)

    def request_approval(
        self, run_id: str, action: str, resource: dict[str, Any]
    ) -> Approval:
        if self.db is None:
            raise RuntimeError("Approval persistence requires a database session.")
        return self._pending_or_approved(run_id, action, resource)

    def decide(self, approval_id: str, approve: bool, actor: str, reason: str) -> Approval:
        if self.db is None:
            raise RuntimeError("Approval persistence requires a database session.")
        approval = self.db.get(Approval, approval_id)
        if not approval:
            raise ValueError(f"Approval {approval_id} not found.")
        if approval.status != "PENDING":
            raise ValueError(f"Approval {approval_id} has already been decided.")
        approval.status = "APPROVED" if approve else "REJECTED"
        approval.decided_by = actor
        approval.decision_reason = reason
        approval.decided_at = _utcnow()
        self.db.commit()
        self.db.refresh(approval)
        return approval

    def _pending_or_approved(
        self, run_id: str, action: str, resource: dict[str, Any]
    ) -> Approval:
        assert self.db is not None
        run = self.db.get(Run, run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found.")
        approvals = (
            self.db.query(Approval)
            .filter(
                Approval.run_id == run_id,
                Approval.action == action,
            )
            .order_by(Approval.requested_at.desc())
            .all()
        )
        approval = next(
            (candidate for candidate in approvals if candidate.resource == resource),
            None,
        )
        if approval:
            return approval
        approval = Approval(
            id=str(uuid.uuid4()),
            run_id=run_id,
            action=action,
            resource=resource,
            status="PENDING",
            requested_at=_utcnow(),
        )
        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)
        return approval

    @staticmethod
    def _evaluate_local(action: str) -> PolicyDecision:
        denied = _csv_env("SACM_DENIED_TOOL_ACTIONS")
        if action in denied:
            return PolicyDecision(False, f"{action} is denied by local policy.")
        approval_actions = _csv_env("SACM_APPROVAL_REQUIRED_ACTIONS")
        return PolicyDecision(
            True,
            "Allowed by local policy.",
            requires_approval=action in approval_actions,
        )

    @staticmethod
    def _evaluate_opa(opa_url: str, payload: dict[str, Any]) -> PolicyDecision:
        try:
            response = httpx.post(
                opa_url.rstrip("/") + "/v1/data/sacm/decision",
                json={"input": payload},
                timeout=5,
            )
            response.raise_for_status()
            result = response.json().get("result")
        except httpx.HTTPError as exc:
            if os.getenv("SACM_OPA_FAIL_CLOSED", "true").lower() == "true":
                return PolicyDecision(False, f"OPA policy evaluation failed: {exc}")
            return PolicyDecision(True, "OPA unavailable; fail-open explicitly configured.")

        if isinstance(result, bool):
            return PolicyDecision(result, "OPA policy decision.")
        if isinstance(result, dict):
            return PolicyDecision(
                bool(result.get("allow", False)),
                str(result.get("reason", "OPA policy decision.")),
                bool(result.get("requires_approval", False)),
            )
        return PolicyDecision(False, "OPA returned an invalid policy decision.")


class ToolGateway:
    """Single authorization gateway for external tools and delivery actions."""

    def __init__(self, policies: PolicyService) -> None:
        self.policies = policies

    def authorize(
        self,
        action: str,
        resource: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> None:
        self.policies.require_allowed(action, resource, run_id=run_id)


def _csv_env(name: str) -> set[str]:
    return {
        value.strip()
        for value in os.getenv(name, "").split(",")
        if value.strip()
    }
