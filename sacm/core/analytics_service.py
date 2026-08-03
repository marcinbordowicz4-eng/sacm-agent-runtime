import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from sacm.core.traceability_service import TraceabilityService
from sacm.infrastructure.db.models import (
    AgentOutcomeAnalytics,
    Approval,
    ContextEvent,
    ExecutionPlan,
    RequirementLink,
    Run,
    RunOutcomeAnalytics,
    RunStep,
    RunStepOutcomeAnalytics,
    RuntimeEvent,
)
from sacm.schemas.analytics import (
    AgentOutcomeAnalyticsV1,
    AggregateOutcomeAnalyticsV1,
    RunOutcomeAnalyticsV1,
    StepOutcomeAnalyticsV1,
)

_ANALYTICS_NAMESPACE = uuid.UUID("523423aa-58bc-4db5-ad3f-21549493ad77")
_EVIDENCE_TYPES = {
    "artifact",
    "commit",
    "diff",
    "evidence_pack",
    "security_finding",
    "test",
    "verification",
}


class AnalyticsNotFoundError(ValueError):
    pass


class AnalyticsService:
    """Deterministically materializes run, step, and agent outcome analytics."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def recompute_run(self, run_id: str) -> RunOutcomeAnalyticsV1:
        run = self.db.get(Run, run_id)
        if run is None:
            raise AnalyticsNotFoundError(f"Run {run_id} not found.")

        trace = TraceabilityService(self.db).refresh(run.task_id)
        links = (
            self.db.query(RequirementLink)
            .filter(RequirementLink.task_id == run.task_id)
            .all()
        )
        plan = (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.task_id == run.task_id)
            .order_by(ExecutionPlan.revision.desc(), ExecutionPlan.id)
            .first()
        )
        runtime_events = (
            self.db.query(RuntimeEvent)
            .filter(RuntimeEvent.run_id == run.id)
            .order_by(RuntimeEvent.sequence, RuntimeEvent.id)
            .all()
        )
        steps = sorted(run.steps, key=lambda item: (item.sequence, item.id))
        agent_events = self._agent_events(run)
        now = datetime.utcnow()

        agent_rows = [
            self._upsert_agent(run, event, legacy, steps, links, now)
            for event, legacy in agent_events
        ]
        agent_by_step: dict[str, list[AgentOutcomeAnalytics]] = {}
        for row in agent_rows:
            if row.step_id:
                agent_by_step.setdefault(row.step_id, []).append(row)

        step_rows = [
            self._upsert_step(
                run,
                step,
                agent_by_step.get(step.id, []),
                links,
                runtime_events,
                now,
            )
            for step in steps
        ]
        self._delete_stale_rows(run.id, step_rows, agent_rows)

        approvals = (
            self.db.query(Approval)
            .filter(Approval.run_id == run.id)
            .order_by(Approval.requested_at, Approval.id)
            .all()
        )
        usage = self._summed_usage(agent_rows)
        run_links = [link for link in links if link.run_id == run.id]
        changed_files = sorted(
            {link.target_id for link in run_links if link.target_type == "changed_file"}
            | self._event_values(runtime_events, "changed_files")
            | {
                str(path)
                for row in agent_rows
                for path in row.details.get("changed_files", [])
            }
        )
        tests = sorted(
            link.target_id for link in run_links if link.target_type == "test"
        )
        verifications = sorted(
            link.target_id for link in run_links if link.target_type == "verification"
        )
        failures = [
            {
                "step_id": step.id,
                "name": step.name,
                "failure": (step.output or {}).get("failure"),
            }
            for step in steps
            if step.status == "FAILED"
        ]
        failures.extend(
            {
                "event_id": event.id,
                "event_type": event.event_type,
                "failure": event.payload.get("failure"),
            }
            for event in runtime_events
            if event.payload.get("failure")
        )

        total_requirements = trace.coverage.total_requirements
        requirement_coverage = (
            trace.coverage.coverage_percent if total_requirements else None
        )
        evidence_coverage = (
            trace.coverage.evidence_coverage_percent if total_requirements else None
        )
        security_findings = (
            list(plan.security_review.findings)
            if plan is not None and plan.security_review is not None
            else None
        )
        replay = run.replay_link
        data_completeness = {
            "tenancy": run.project_id is not None,
            "timing": run.started_at is not None
            and (run.completed_at is not None or self._outcome(run.status) is None),
            "usage": bool(agent_rows) and usage["has_usage"],
            "cost": usage["cost_estimation_available"],
            "requirements": total_requirements > 0,
            "application_context": run.task.application_context is not None,
            "execution_plan": plan is not None,
            "security_review": security_findings is not None,
            "replay_linkage": replay is not None or bool(run.replay_source_links),
        }
        legacy_data = (
            run.project_id is None
            or run.task.task_contract is None
            or any(row.legacy_attribution for row in agent_rows)
        )
        data_state = (
            "legacy"
            if legacy_data
            else "complete"
            if all(
                data_completeness[key]
                for key in (
                    "tenancy",
                    "usage",
                    "requirements",
                    "application_context",
                    "execution_plan",
                    "security_review",
                )
            )
            else "partial"
        )
        details = {
            "data_state": data_state,
            "usage": usage["breakdown"],
            "changed_files": changed_files,
            "tests": tests,
            "verifications": verifications,
            "failures": failures,
            "uncovered_requirements": [
                {
                    "id": item.id,
                    "title": item.title,
                    "text": item.text,
                }
                for item in trace.coverage.uncovered_requirements
            ],
            "requirement_counts": {
                "total": total_requirements,
                "covered": trace.coverage.covered_requirements,
                "evidence_covered": trace.coverage.evidence_covered_requirements,
            },
            "approval_statuses": [approval.status for approval in approvals],
            "security_findings": security_findings or [],
            "source_revision": run.source_revision,
            "snapshot_count": len(run.snapshots),
            "latest_snapshot_id": run.snapshots[-1].id if run.snapshots else None,
            "replay_run_ids": sorted(
                link.replay_run_id for link in run.replay_source_links
            ),
        }
        values: dict[str, Any] = {
            "id": self._stable_id("run", run.id),
            "run_id": run.id,
            "task_id": run.task_id,
            "project_id": run.project_id,
            "schema_version": "outcome-analytics/v1",
            "status": run.status,
            "outcome": self._outcome(run.status),
            "latency_ms": self._latency(run.started_at, run.completed_at),
            "retry_count": sum(step.retry_count for step in steps),
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "estimated_cost_usd": usage["estimated_cost_usd"],
            "cost_estimation_available": usage["cost_estimation_available"],
            "evidence_pack_count": len(run.evidence_packs),
            "evidence_coverage_percent": evidence_coverage,
            "requirement_coverage_percent": requirement_coverage,
            "policy_blocked": (
                plan.status == "BLOCKED"
                or not bool(plan.policy_decision.decision.get("allow", False))
                if plan is not None and plan.policy_decision is not None
                else None
            ),
            "approval_count": len(approvals),
            "pending_approval_count": self._status_count(approvals, "PENDING"),
            "approved_approval_count": self._status_count(approvals, "APPROVED"),
            "rejected_approval_count": self._status_count(approvals, "REJECTED"),
            "security_finding_count": (
                len(security_findings) if security_findings is not None else None
            ),
            "open_security_finding_count": (
                sum(
                    str(item.get("status", "open")).lower() == "open"
                    for item in security_findings
                )
                if security_findings is not None
                else None
            ),
            "high_critical_security_finding_count": (
                sum(
                    str(item.get("severity", "")).lower() in {"high", "critical"}
                    for item in security_findings
                )
                if security_findings is not None
                else None
            ),
            "source_run_id": replay.source_run_id if replay else None,
            "source_snapshot_id": replay.source_snapshot_id if replay else None,
            "replay_count": len(run.replay_source_links),
            "changed_file_count": len(changed_files),
            "test_count": max(len(tests), sum(row.test_count for row in agent_rows)),
            "verification_count": max(
                len(verifications),
                sum(row.verification_count for row in agent_rows),
            ),
            "step_count": len(step_rows),
            "agent_invocation_count": len(agent_rows),
            "legacy_data": legacy_data,
            "data_completeness": data_completeness,
            "details": details,
        }
        fingerprint = self._fingerprint(values)
        analytics = (
            self.db.query(RunOutcomeAnalytics)
            .filter(RunOutcomeAnalytics.run_id == run.id)
            .first()
        )
        analytics = self._upsert(
            analytics,
            RunOutcomeAnalytics,
            values,
            fingerprint,
            now,
        )
        self.db.commit()
        self.db.refresh(analytics)
        return self._run_read(analytics, step_rows, agent_rows)

    def aggregate(
        self,
        scope_type: str,
        scope_id: str,
        runs: Iterable[Run],
        *,
        scope_name: str | None = None,
    ) -> AggregateOutcomeAnalyticsV1:
        run_reads = [
            self.recompute_run(run.id)
            for run in sorted(runs, key=lambda item: (item.created_at, item.id))
        ]
        outcomes = [item.outcome for item in run_reads]
        latencies = [
            item.latency_ms for item in run_reads if item.latency_ms is not None
        ]
        evidence_coverage = [
            item.evidence_coverage_percent
            for item in run_reads
            if item.evidence_coverage_percent is not None
        ]
        requirement_coverage = [
            item.requirement_coverage_percent
            for item in run_reads
            if item.requirement_coverage_percent is not None
        ]
        costs = [
            item.estimated_cost_usd
            for item in run_reads
            if item.estimated_cost_usd is not None
        ]
        input_tokens = [
            item.input_tokens for item in run_reads if item.input_tokens is not None
        ]
        output_tokens = [
            item.output_tokens for item in run_reads if item.output_tokens is not None
        ]
        security_counts = [
            item.security_finding_count
            for item in run_reads
            if item.security_finding_count is not None
        ]
        open_security_counts = [
            item.open_security_finding_count
            for item in run_reads
            if item.open_security_finding_count is not None
        ]
        terminal_count = sum(outcome is not None for outcome in outcomes)
        success_count = outcomes.count("success")
        return AggregateOutcomeAnalyticsV1(
            scope_type=scope_type,  # type: ignore[arg-type]
            scope_id=scope_id,
            scope_name=scope_name,
            run_count=len(run_reads),
            terminal_run_count=terminal_count,
            success_count=success_count,
            failure_count=outcomes.count("failure"),
            cancelled_count=outcomes.count("cancelled"),
            success_rate_percent=(
                round(100 * success_count / terminal_count, 2)
                if terminal_count
                else None
            ),
            average_latency_ms=(
                round(sum(latencies) / len(latencies), 2) if latencies else None
            ),
            retry_count=sum(item.retry_count for item in run_reads),
            input_tokens=sum(input_tokens) if input_tokens else None,
            output_tokens=sum(output_tokens) if output_tokens else None,
            estimated_cost_usd=round(sum(costs), 8) if costs else None,
            cost_estimation_available=any(
                item.cost_estimation_available for item in run_reads
            ),
            evidence_pack_count=sum(item.evidence_pack_count for item in run_reads),
            average_evidence_coverage_percent=(
                round(sum(evidence_coverage) / len(evidence_coverage), 2)
                if evidence_coverage
                else None
            ),
            average_requirement_coverage_percent=(
                round(sum(requirement_coverage) / len(requirement_coverage), 2)
                if requirement_coverage
                else None
            ),
            policy_blocked_run_count=sum(
                item.policy_blocked is True for item in run_reads
            ),
            approval_count=sum(item.approval_count for item in run_reads),
            pending_approval_count=sum(
                item.pending_approval_count for item in run_reads
            ),
            security_finding_count=(sum(security_counts) if security_counts else None),
            open_security_finding_count=(
                sum(open_security_counts) if open_security_counts else None
            ),
            changed_file_count=sum(item.changed_file_count for item in run_reads),
            test_count=sum(item.test_count for item in run_reads),
            verification_count=sum(item.verification_count for item in run_reads),
            step_count=sum(item.step_count for item in run_reads),
            agent_invocation_count=sum(
                item.agent_invocation_count for item in run_reads
            ),
            legacy_run_count=sum(item.legacy_data for item in run_reads),
            incomplete_run_count=sum(
                item.data_state != "complete" for item in run_reads
            ),
            runs=run_reads,
            computed_at=datetime.utcnow(),
        )

    def _upsert_agent(
        self,
        run: Run,
        event: ContextEvent,
        legacy: bool,
        steps: list[RunStep],
        links: list[RequirementLink],
        now: datetime,
    ) -> AgentOutcomeAnalytics:
        payload = event.payload
        task_contract = self._mapping(payload.get("agent_task_contract"))
        result = self._mapping(payload.get("agent_result_contract"))
        step_id = task_contract.get("step_id") or result.get("step_id")
        step = next((item for item in steps if item.id == step_id), None)
        usage_records = self._usage_records(payload)
        usage = self._usage_values(usage_records)
        artifacts = [
            item
            for key in ("artifacts", "evidence")
            for item in result.get(key, [])
            if isinstance(item, dict)
        ]
        artifact_types = [
            str(item.get("artifact_type", "")).lower() for item in artifacts
        ]
        tool_records = [
            item for item in payload.get("tool_execution", []) if isinstance(item, dict)
        ]
        changed_files = {
            str(path)
            for item in artifacts
            for path in self._artifact_changed_files(item)
        }
        requirement_ids = {
            link.requirement_id
            for link in links
            if link.run_id == run.id
            and (
                link.target_id == event.id
                or step is not None
                and link.target_id == step.id
            )
        }
        agent_name = str(payload.get("agent_name") or "unknown-agent")
        framework: str | None
        if ":" in agent_name:
            framework, plain_name = agent_name.split(":", 1)
            native_agent = False
        else:
            framework = (
                str(payload.get("framework"))
                if payload.get("framework")
                else str(step.input_.get("framework"))
                if step and step.input_.get("framework")
                else "native"
            )
            plain_name = agent_name
            native_agent = True
        status = result.get("status")
        failure = self._mapping(result.get("failure")) or None
        latency = (
            self._latency(step.started_at, step.completed_at)
            if step is not None
            else self._tool_duration(tool_records)
        )
        values: dict[str, Any] = {
            "id": self._stable_id("agent", event.id),
            "run_id": run.id,
            "step_id": step.id if step else None,
            "source_event_id": event.id,
            "schema_version": "agent-outcome-analytics/v1",
            "agent_name": plain_name,
            "role": task_contract.get("role"),
            "provider": (
                usage_records[0].get("provider")
                if usage_records
                else payload.get("provider") or ("sacm" if native_agent else None)
            ),
            "model_name": (
                usage_records[0].get("model")
                if usage_records
                else payload.get("model") or ("deterministic" if native_agent else None)
            ),
            "framework": framework,
            "status": str(status) if status is not None else None,
            "outcome": self._outcome(str(status)) if status is not None else None,
            "latency_ms": latency,
            "retry_count": step.retry_count if step else 0,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "estimated_cost_usd": usage["estimated_cost_usd"],
            "evidence_count": len(artifacts),
            "requirement_count": len(requirement_ids),
            "security_finding_count": len(
                [item for item in result.get("findings", []) if isinstance(item, dict)]
            ),
            "changed_file_count": len(changed_files),
            "test_count": sum("test" in value for value in artifact_types),
            "verification_count": sum(
                "verification" in value for value in artifact_types
            ),
            "failure": failure,
            "details": {
                "summary": payload.get("summary") or result.get("summary"),
                "confidence": payload.get("confidence") or result.get("confidence"),
                "next_state_hint": payload.get("next_state_hint")
                or result.get("next_state_hint"),
                "usage": usage_records,
                "tool_execution_count": len(tool_records),
                "failed_tool_execution_count": sum(
                    item.get("returncode") not in {None, 0} for item in tool_records
                ),
                "changed_files": sorted(changed_files),
            },
            "legacy_attribution": legacy,
        }
        fingerprint = self._fingerprint(values)
        row = (
            self.db.query(AgentOutcomeAnalytics)
            .filter(AgentOutcomeAnalytics.source_event_id == event.id)
            .first()
        )
        return self._upsert(
            row,
            AgentOutcomeAnalytics,
            values,
            fingerprint,
            now,
        )

    def _upsert_step(
        self,
        run: Run,
        step: RunStep,
        agents: list[AgentOutcomeAnalytics],
        links: list[RequirementLink],
        events: list[RuntimeEvent],
        now: datetime,
    ) -> RunStepOutcomeAnalytics:
        step_links = [
            link
            for link in links
            if link.run_id == run.id
            and link.target_type == "run_step"
            and link.target_id == step.id
        ]
        event_values = [event for event in events if event.step_id == step.id]
        input_tokens = [
            item.input_tokens for item in agents if item.input_tokens is not None
        ]
        output_tokens = [
            item.output_tokens for item in agents if item.output_tokens is not None
        ]
        costs = [
            item.estimated_cost_usd
            for item in agents
            if item.estimated_cost_usd is not None
        ]
        agent = agents[0] if agents else None
        changed_files = {
            value
            for event in event_values
            for value in self._payload_values(event.payload, "changed_files")
        }
        failure = self._mapping((step.output or {}).get("failure")) or None
        values: dict[str, Any] = {
            "id": self._stable_id("step", step.id),
            "run_id": run.id,
            "step_id": step.id,
            "schema_version": "step-outcome-analytics/v1",
            "sequence": step.sequence,
            "name": step.name,
            "status": step.status,
            "outcome": self._outcome(step.status),
            "latency_ms": self._latency(step.started_at, step.completed_at),
            "retry_count": step.retry_count,
            "agent_name": agent.agent_name if agent else step.input_.get("agent_name"),
            "provider": agent.provider if agent else None,
            "model_name": agent.model_name if agent else None,
            "framework": agent.framework if agent else step.input_.get("framework"),
            "input_tokens": sum(input_tokens) if input_tokens else None,
            "output_tokens": sum(output_tokens) if output_tokens else None,
            "estimated_cost_usd": round(sum(costs), 8) if costs else None,
            "evidence_count": sum(item.evidence_count for item in agents),
            "requirement_count": len({link.requirement_id for link in step_links}),
            "changed_file_count": len(changed_files)
            + sum(item.changed_file_count for item in agents),
            "test_count": sum(item.test_count for item in agents),
            "verification_count": sum(item.verification_count for item in agents),
            "failure": failure,
            "details": {
                "idempotency_key": step.idempotency_key,
                "execution_plan_step_id": step.input_.get("execution_plan_step_id"),
                "runtime_event_count": len(event_values),
                "changed_files": sorted(changed_files),
            },
        }
        fingerprint = self._fingerprint(values)
        row = (
            self.db.query(RunStepOutcomeAnalytics)
            .filter(RunStepOutcomeAnalytics.step_id == step.id)
            .first()
        )
        return self._upsert(
            row,
            RunStepOutcomeAnalytics,
            values,
            fingerprint,
            now,
        )

    def _agent_events(self, run: Run) -> list[tuple[ContextEvent, bool]]:
        events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == run.task_id,
                ContextEvent.event_type == "agent_result",
            )
            .order_by(ContextEvent.created_at, ContextEvent.id)
            .all()
        )
        task_run_count = self.db.query(Run).filter(Run.task_id == run.task_id).count()
        result: list[tuple[ContextEvent, bool]] = []
        for event in events:
            event_run_id = self._event_run_id(event.payload)
            if event_run_id == run.id:
                result.append((event, False))
            elif event_run_id is None and task_run_count == 1:
                result.append((event, True))
        return result

    @staticmethod
    def _event_run_id(payload: dict[str, Any]) -> str | None:
        for value in (
            payload.get("run_id"),
            AnalyticsService._mapping(payload.get("agent_task_contract")).get("run_id"),
            AnalyticsService._mapping(payload.get("agent_result_contract")).get(
                "run_id"
            ),
        ):
            if isinstance(value, str):
                return value
        return None

    def _delete_stale_rows(
        self,
        run_id: str,
        steps: list[RunStepOutcomeAnalytics],
        agents: list[AgentOutcomeAnalytics],
    ) -> None:
        step_ids = {item.id for item in steps}
        agent_ids = {item.id for item in agents}
        for step_row in (
            self.db.query(RunStepOutcomeAnalytics)
            .filter(RunStepOutcomeAnalytics.run_id == run_id)
            .all()
        ):
            if step_row.id not in step_ids:
                self.db.delete(step_row)
        for agent_row in (
            self.db.query(AgentOutcomeAnalytics)
            .filter(AgentOutcomeAnalytics.run_id == run_id)
            .all()
        ):
            if agent_row.id not in agent_ids:
                self.db.delete(agent_row)

    def _upsert(
        self,
        row: Any,
        model: type[Any],
        values: dict[str, Any],
        fingerprint: str,
        now: datetime,
    ) -> Any:
        if row is not None and row.source_fingerprint == fingerprint:
            return row
        if row is None:
            row = model()
            self.db.add(row)
        for key, value in values.items():
            setattr(row, key, value)
        row.source_fingerprint = fingerprint
        row.computed_at = now
        self.db.flush()
        return row

    @staticmethod
    def _outcome(status: str) -> str | None:
        normalized = status.upper()
        if normalized in {"COMPLETED", "SUCCESS", "SUCCEEDED", "PASSED"}:
            return "success"
        if normalized in {"FAILED", "FAILURE", "ERROR"}:
            return "failure"
        if normalized in {"CANCELLED", "CANCELED"}:
            return "cancelled"
        return None

    @staticmethod
    def _latency(
        started_at: datetime | None, completed_at: datetime | None
    ) -> int | None:
        if started_at is None or completed_at is None:
            return None
        return max(0, round((completed_at - started_at).total_seconds() * 1000))

    @staticmethod
    def _tool_duration(records: list[dict[str, Any]]) -> int | None:
        values = [
            item["duration_ms"]
            for item in records
            if isinstance(item.get("duration_ms"), int)
        ]
        return sum(values) if values else None

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @classmethod
    def _usage_records(cls, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = [
            *(
                payload.get("usage", [])
                if isinstance(payload.get("usage"), list)
                else []
            ),
            *(
                cls._mapping(payload.get("agent_result_contract")).get("usage", [])
                if isinstance(
                    cls._mapping(payload.get("agent_result_contract")).get("usage"),
                    list,
                )
                else []
            ),
        ]
        result: list[dict[str, Any]] = []
        fingerprints: set[str] = set()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            record = {
                key: item.get(key)
                for key in (
                    "provider",
                    "model",
                    "input_tokens",
                    "output_tokens",
                    "estimated_cost_usd",
                )
            }
            if not isinstance(record["provider"], str) or not isinstance(
                record["model"], str
            ):
                continue
            fingerprint = json.dumps(record, sort_keys=True, default=str)
            if fingerprint not in fingerprints:
                fingerprints.add(fingerprint)
                result.append(record)
        return result

    @staticmethod
    def _usage_values(records: list[dict[str, Any]]) -> dict[str, Any]:
        if not records:
            return {
                "input_tokens": None,
                "output_tokens": None,
                "estimated_cost_usd": None,
                "cost_estimation_available": False,
            }
        costs = [
            float(item["estimated_cost_usd"])
            for item in records
            if isinstance(item.get("estimated_cost_usd"), (int, float))
        ]
        return {
            "input_tokens": sum(int(item.get("input_tokens") or 0) for item in records),
            "output_tokens": sum(
                int(item.get("output_tokens") or 0) for item in records
            ),
            "estimated_cost_usd": round(sum(costs), 8) if costs else None,
            "cost_estimation_available": bool(costs),
        }

    @staticmethod
    def _summed_usage(rows: list[AgentOutcomeAnalytics]) -> dict[str, Any]:
        with_usage = [
            row
            for row in rows
            if row.input_tokens is not None or row.output_tokens is not None
        ]
        costs = [
            row.estimated_cost_usd for row in rows if row.estimated_cost_usd is not None
        ]
        breakdown = [
            {
                "agent_name": row.agent_name,
                "provider": row.provider,
                "model": row.model_name,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "estimated_cost_usd": row.estimated_cost_usd,
            }
            for row in rows
            if row.input_tokens is not None
            or row.output_tokens is not None
            or row.estimated_cost_usd is not None
        ]
        return {
            "has_usage": bool(with_usage),
            "input_tokens": (
                sum(row.input_tokens or 0 for row in with_usage) if with_usage else None
            ),
            "output_tokens": (
                sum(row.output_tokens or 0 for row in with_usage)
                if with_usage
                else None
            ),
            "estimated_cost_usd": round(sum(costs), 8) if costs else None,
            "cost_estimation_available": bool(costs),
            "breakdown": breakdown,
        }

    @staticmethod
    def _artifact_changed_files(item: dict[str, Any]) -> list[str]:
        metadata = item.get("metadata")
        values = (
            metadata.get("changed_files", [])
            if isinstance(metadata, dict)
            else item.get("changed_files", [])
        )
        return [str(value) for value in values] if isinstance(values, list) else []

    @staticmethod
    def _payload_values(payload: dict[str, Any], key: str) -> set[str]:
        value = payload.get(key, [])
        return (
            {str(item) for item in value if item} if isinstance(value, list) else set()
        )

    @classmethod
    def _event_values(cls, events: list[RuntimeEvent], key: str) -> set[str]:
        return {
            value
            for event in events
            for value in cls._payload_values(event.payload, key)
        }

    @staticmethod
    def _status_count(items: list[Approval], status: str) -> int:
        return sum(item.status.upper() == status for item in items)

    @staticmethod
    def _stable_id(kind: str, source_id: str) -> str:
        return str(uuid.uuid5(_ANALYTICS_NAMESPACE, f"{kind}:{source_id}"))

    @staticmethod
    def _fingerprint(values: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                values,
                sort_keys=True,
                separators=(",", ":"),
                default=lambda value: (
                    value.isoformat() if isinstance(value, datetime) else str(value)
                ),
            ).encode()
        ).hexdigest()

    @staticmethod
    def _run_read(
        row: RunOutcomeAnalytics,
        steps: list[RunStepOutcomeAnalytics],
        agents: list[AgentOutcomeAnalytics],
    ) -> RunOutcomeAnalyticsV1:
        organization_id = (
            row.run.project.organization_id if row.run.project is not None else None
        )
        return RunOutcomeAnalyticsV1(
            schema_version="outcome-analytics/v1",
            run_id=row.run_id,
            task_id=row.task_id,
            project_id=row.project_id,
            organization_id=organization_id,
            status=row.status,
            outcome=row.outcome,  # type: ignore[arg-type]
            latency_ms=row.latency_ms,
            retry_count=row.retry_count,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            estimated_cost_usd=row.estimated_cost_usd,
            cost_estimation_available=row.cost_estimation_available,
            evidence_pack_count=row.evidence_pack_count,
            evidence_coverage_percent=row.evidence_coverage_percent,
            requirement_coverage_percent=row.requirement_coverage_percent,
            policy_blocked=row.policy_blocked,
            approval_count=row.approval_count,
            pending_approval_count=row.pending_approval_count,
            approved_approval_count=row.approved_approval_count,
            rejected_approval_count=row.rejected_approval_count,
            security_finding_count=row.security_finding_count,
            open_security_finding_count=row.open_security_finding_count,
            high_critical_security_finding_count=(
                row.high_critical_security_finding_count
            ),
            source_run_id=row.source_run_id,
            source_snapshot_id=row.source_snapshot_id,
            replay_count=row.replay_count,
            changed_file_count=row.changed_file_count,
            test_count=row.test_count,
            verification_count=row.verification_count,
            step_count=row.step_count,
            agent_invocation_count=row.agent_invocation_count,
            legacy_data=row.legacy_data,
            data_state=row.details.get("data_state", "partial"),
            data_completeness=row.data_completeness,
            details=row.details,
            steps=[
                StepOutcomeAnalyticsV1(
                    schema_version="step-outcome-analytics/v1",
                    step_id=item.step_id,
                    sequence=item.sequence,
                    name=item.name,
                    status=item.status,
                    outcome=item.outcome,  # type: ignore[arg-type]
                    latency_ms=item.latency_ms,
                    retry_count=item.retry_count,
                    agent_name=item.agent_name,
                    provider=item.provider,
                    model=item.model_name,
                    framework=item.framework,
                    input_tokens=item.input_tokens,
                    output_tokens=item.output_tokens,
                    estimated_cost_usd=item.estimated_cost_usd,
                    evidence_count=item.evidence_count,
                    requirement_count=item.requirement_count,
                    changed_file_count=item.changed_file_count,
                    test_count=item.test_count,
                    verification_count=item.verification_count,
                    failure=item.failure,
                    details=item.details,
                    computed_at=item.computed_at,
                )
                for item in sorted(steps, key=lambda value: value.sequence)
            ],
            agents=[
                AgentOutcomeAnalyticsV1(
                    schema_version="agent-outcome-analytics/v1",
                    invocation_id=item.id,
                    source_event_id=item.source_event_id,
                    step_id=item.step_id,
                    agent_name=item.agent_name,
                    role=item.role,
                    provider=item.provider,
                    model=item.model_name,
                    framework=item.framework,
                    status=item.status,
                    outcome=item.outcome,  # type: ignore[arg-type]
                    latency_ms=item.latency_ms,
                    retry_count=item.retry_count,
                    input_tokens=item.input_tokens,
                    output_tokens=item.output_tokens,
                    estimated_cost_usd=item.estimated_cost_usd,
                    evidence_count=item.evidence_count,
                    requirement_count=item.requirement_count,
                    security_finding_count=item.security_finding_count,
                    changed_file_count=item.changed_file_count,
                    test_count=item.test_count,
                    verification_count=item.verification_count,
                    failure=item.failure,
                    legacy_attribution=item.legacy_attribution,
                    details=item.details,
                    computed_at=item.computed_at,
                )
                for item in sorted(
                    agents, key=lambda value: (value.computed_at, value.id)
                )
            ],
            computed_at=row.computed_at,
        )
