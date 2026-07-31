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

    def evaluate_execution_plan(
        self,
        input_document: dict[str, Any],
        *,
        policy_pack: str,
    ) -> dict[str, Any]:
        if policy_pack not in {"default", "strict"}:
            raise ValueError(f"Unknown execution policy pack: {policy_pack}")
        payload = {"policy_pack": policy_pack, **input_document}
        opa_url = os.getenv("SACM_OPA_URL")
        if opa_url:
            return self._evaluate_execution_plan_opa(opa_url, payload)
        return self._evaluate_execution_plan_local(payload)

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

    @staticmethod
    def _evaluate_execution_plan_local(
        input_document: dict[str, Any],
    ) -> dict[str, Any]:
        pack = str(input_document["policy_pack"])
        risk = input_document.get("risk", {})
        risk_level = str(risk.get("level", "critical"))
        steps = list(input_document.get("steps", []))
        privileged_tools = {
            "deployment.execute",
            "repository.admin",
            "schema.migrate",
            "secrets.resolve",
            "workspace.execute",
        }

        matched_rules: list[dict[str, Any]] = [
            {
                "rule_id": "security-review-required",
                "effect": "security_review",
                "reason": "Every execution plan requires an independent security review.",
                "step_ids": [
                    str(step["id"])
                    for step in steps
                    if step.get("kind") == "security_review"
                ],
            }
        ]
        gate_candidates: list[dict[str, Any]] = []

        risk_threshold = {"default": {"high", "critical"}, "strict": {"medium", "high", "critical"}}
        if risk_level in risk_threshold[pack]:
            affected = [str(step["id"]) for step in steps]
            matched_rules.append(
                {
                    "rule_id": f"{pack}-application-risk",
                    "effect": "approval",
                    "reason": f"{risk_level} application risk requires approval under the {pack} pack.",
                    "step_ids": affected,
                }
            )
            gate_candidates.append(
                {
                    "gate_type": "application_risk",
                    "action": "execution.start",
                    "reason": f"Application risk is {risk_level}.",
                    "step_ids": affected,
                }
            )

        privileged_steps = [
            step
            for step in steps
            if privileged_tools.intersection(step.get("required_tools", []))
        ]
        if privileged_steps:
            affected = [str(step["id"]) for step in privileged_steps]
            matched_rules.append(
                {
                    "rule_id": "privileged-tools",
                    "effect": "approval",
                    "reason": "Privileged tools require explicit approval.",
                    "step_ids": affected,
                }
            )
            gate_candidates.append(
                {
                    "gate_type": "privileged_tools",
                    "action": "tools.privileged",
                    "reason": "One or more steps request privileged tools.",
                    "step_ids": affected,
                }
            )

        for tag, gate_type, action, reason in (
            (
                "deployment",
                "deployment",
                "deployment.execute",
                "Deployment work requires explicit approval.",
            ),
            (
                "schema",
                "schema_change",
                "schema.migrate",
                "Schema-changing work requires explicit approval.",
            ),
        ):
            affected = [
                str(step["id"])
                for step in steps
                if tag in step.get("risk_tags", [])
            ]
            if affected:
                matched_rules.append(
                    {
                        "rule_id": f"{tag}-sensitive-work",
                        "effect": "approval",
                        "reason": reason,
                        "step_ids": affected,
                    }
                )
                gate_candidates.append(
                    {
                        "gate_type": gate_type,
                        "action": action,
                        "reason": reason,
                        "step_ids": affected,
                    }
                )

        security_sensitive = [
            str(step["id"])
            for step in steps
            if "security_sensitive" in step.get("risk_tags", [])
        ]
        if security_sensitive:
            matched_rules.append(
                {
                    "rule_id": "security-sensitive-work",
                    "effect": "security_review",
                    "reason": "Security-sensitive work requires focused reviewer coverage.",
                    "step_ids": security_sensitive,
                }
            )
            if pack == "strict":
                gate_candidates.append(
                    {
                        "gate_type": "security_sensitive",
                        "action": "security.change",
                        "reason": "Strict policy requires approval for security-sensitive work.",
                        "step_ids": security_sensitive,
                    }
                )

        approval_gates: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in gate_candidates:
            key = (candidate["gate_type"], candidate["action"])
            if key not in seen:
                seen.add(key)
                approval_gates.append(candidate)

        denials: list[str] = []
        if pack == "strict" and risk_level == "critical" and any(
            "deployment" in step.get("risk_tags", []) for step in steps
        ):
            denials.append(
                "Strict policy denies critical-risk deployment plans until risk is reduced."
            )
            matched_rules.append(
                {
                    "rule_id": "strict-critical-deployment",
                    "effect": "deny",
                    "reason": denials[-1],
                    "step_ids": [
                        str(step["id"])
                        for step in steps
                        if "deployment" in step.get("risk_tags", [])
                    ],
                }
            )

        return {
            "schema_version": "policy-decision/v1",
            "pack": pack,
            "allow": not denials,
            "requires_approval": bool(approval_gates),
            "requires_security_review": True,
            "matched_rules": matched_rules,
            "approval_gates": approval_gates,
            "denials": denials,
            "input": input_document,
        }

    @staticmethod
    def _evaluate_execution_plan_opa(
        opa_url: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            response = httpx.post(
                opa_url.rstrip("/") + "/v1/data/sacm/execution_plan/decision",
                json={"input": payload},
                timeout=5,
            )
            response.raise_for_status()
            result = response.json().get("result")
        except httpx.HTTPError:
            return {
                "schema_version": "policy-decision/v1",
                "pack": payload["policy_pack"],
                "allow": False,
                "requires_approval": False,
                "requires_security_review": True,
                "matched_rules": [
                    {
                        "rule_id": "opa-fail-closed",
                        "effect": "deny",
                        "reason": "OPA execution policy evaluation failed closed.",
                        "step_ids": [],
                    }
                ],
                "approval_gates": [],
                "denials": ["OPA execution policy evaluation failed closed."],
                "input": payload,
            }
        if not isinstance(result, dict):
            raise ValueError("OPA returned an invalid execution policy decision.")
        return {
            "schema_version": "policy-decision/v1",
            "pack": payload["policy_pack"],
            "allow": bool(result.get("allow", False)),
            "requires_approval": bool(result.get("requires_approval", False)),
            "requires_security_review": bool(
                result.get("requires_security_review", True)
            ),
            "matched_rules": list(result.get("matched_rules", [])),
            "approval_gates": list(result.get("approval_gates", [])),
            "denials": list(result.get("denials", [])),
            "input": payload,
        }


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
