import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, cast

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import (
    Artifact,
    ContextEvent,
    EvidencePack,
    ExecutionPlan,
    Requirement,
    RequirementLink,
    Run,
    RunStep,
    RuntimeEvent,
    Task,
)
from sacm.schemas.task import TaskContractV1
from sacm.schemas.traceability import (
    RequirementCoverageV1,
    RequirementLinkCreateV1,
    RequirementLinkSource,
    RequirementLinkV1,
    RequirementV1,
    TraceabilityTargetType,
    TraceabilityV1,
)

TRACEABILITY_NAMESPACE = uuid.UUID("91474583-a973-4d5f-a68a-55d239ea03a2")
_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w]+")
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "secret_value",
    "token",
}
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|authorization|credential|password|private_?key|secret|token)(?:$|_)",
    re.IGNORECASE,
)
_EVIDENCE_TARGETS = {
    "artifact",
    "commit",
    "diff",
    "evidence_pack",
    "security_finding",
    "test",
    "verification",
}


class TraceabilityError(ValueError):
    pass


class TraceabilityNotFoundError(TraceabilityError):
    pass


class TraceabilityService:
    """Builds normalized requirements and links them to persisted SACM records."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def refresh(self, task_id: str) -> TraceabilityV1:
        task = self.db.get(Task, task_id)
        if task is None:
            raise TraceabilityNotFoundError(f"Task {task_id} not found.")
        candidates = self._requirement_candidates(task)
        requirements = self._persist_requirements(task, candidates)
        self._replace_derived_links(task, requirements)
        self.db.commit()
        return self.get(task_id, refresh=False)

    def get(self, task_id: str, *, refresh: bool = True) -> TraceabilityV1:
        if refresh:
            return self.refresh(task_id)
        if self.db.get(Task, task_id) is None:
            raise TraceabilityNotFoundError(f"Task {task_id} not found.")
        requirements = (
            self.db.query(Requirement)
            .filter(Requirement.task_id == task_id)
            .order_by(Requirement.position, Requirement.id)
            .all()
        )
        links = (
            self.db.query(RequirementLink)
            .filter(RequirementLink.task_id == task_id)
            .order_by(
                RequirementLink.requirement_id,
                RequirementLink.target_type,
                RequirementLink.target_id,
                RequirementLink.relation,
            )
            .all()
        )
        requirement_reads = [self._requirement_read(item) for item in requirements]
        return TraceabilityV1(
            task_id=task_id,
            requirements=requirement_reads,
            links=[self._link_read(item) for item in links],
            coverage=self._coverage(requirement_reads, links),
            refreshed_at=datetime.utcnow(),
        )

    def submit_link(
        self,
        task_id: str,
        payload: RequirementLinkCreateV1,
        *,
        actor: str,
    ) -> RequirementLinkV1:
        requirement = self.db.get(Requirement, payload.requirement_id)
        if requirement is None or requirement.task_id != task_id:
            raise TraceabilityNotFoundError(
                f"Requirement {payload.requirement_id} not found for task {task_id}."
            )
        if payload.run_id is not None:
            run = self.db.get(Run, payload.run_id)
            if run is None or run.task_id != task_id:
                raise TraceabilityError(
                    f"Run {payload.run_id} does not belong to task {task_id}."
                )
        if self._contains_secret_material(payload.metadata):
            raise TraceabilityError(
                "Traceability link metadata must not contain secret material."
            )
        link = self._link(
            requirement,
            payload.target_type,
            payload.target_id,
            payload.relation,
            run_id=payload.run_id,
            source="external",
            metadata=payload.metadata,
            created_by=actor,
        )
        self.db.commit()
        self.db.refresh(link)
        return self._link_read(link)

    def _requirement_candidates(
        self, task: Task
    ) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        if task.task_contract:
            contract = TaskContractV1.model_validate(task.task_contract)
            for position, text in enumerate(contract.acceptance_criteria, start=1):
                self._add_candidate(
                    candidates,
                    text,
                    {
                        "source_type": "task_contract",
                        "source_id": f"{task.id}:acceptance:{position}",
                        "field": "acceptance_criteria",
                        "position": position,
                    },
                )

        bdd_events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == task.id,
                ContextEvent.event_type == "bdd_requirement_registered",
            )
            .order_by(ContextEvent.created_at, ContextEvent.id)
            .all()
        )
        for event in bdd_events:
            scenarios = event.payload.get("scenarios", [])
            if not isinstance(scenarios, list):
                continue
            for position, scenario in enumerate(scenarios, start=1):
                if not isinstance(scenario, dict):
                    continue
                name = str(scenario.get("name", "")).strip()
                steps = scenario.get("steps", [])
                rendered_steps = [
                    f"{step.get('keyword', '')} {step.get('text', '')}".strip()
                    for step in steps
                    if isinstance(step, dict) and step.get("text")
                ]
                text = ". ".join(part for part in [name, *rendered_steps] if part)
                self._add_candidate(
                    candidates,
                    text,
                    {
                        "source_type": "bdd_event",
                        "source_id": event.id,
                        "scenario_position": position,
                        "scenario_name": name,
                        "requirement_hash": event.payload.get("requirement_hash"),
                    },
                )
        return list(candidates.values())

    def _add_candidate(
        self,
        candidates: dict[str, dict[str, Any]],
        text: str,
        source_ref: dict[str, Any],
    ) -> None:
        clean_text = _SPACE.sub(" ", text).strip()
        normalized = self.normalize(clean_text)
        if not normalized:
            return
        candidate = candidates.setdefault(
            normalized,
            {
                "text": clean_text,
                "normalized_text": normalized,
                "source_refs": [],
            },
        )
        if source_ref not in candidate["source_refs"]:
            candidate["source_refs"].append(source_ref)

    def _persist_requirements(
        self, task: Task, candidates: list[dict[str, Any]]
    ) -> list[Requirement]:
        existing = {
            item.stable_hash: item
            for item in self.db.query(Requirement)
            .filter(Requirement.task_id == task.id)
            .all()
        }
        for item in existing.values():
            item.position = -item.position
        self.db.flush()

        retained: set[str] = set()
        requirements: list[Requirement] = []
        for position, candidate in enumerate(candidates, start=1):
            stable_hash = self.requirement_hash(candidate["normalized_text"])
            requirement_id = str(
                uuid.uuid5(TRACEABILITY_NAMESPACE, f"{task.id}:{stable_hash}")
            )
            requirement = existing.get(stable_hash)
            if requirement is None:
                requirement = Requirement(
                    id=requirement_id,
                    task_id=task.id,
                    stable_hash=stable_hash,
                )
                self.db.add(requirement)
            requirement.schema_version = "requirement/v1"
            requirement.position = position
            requirement.title = self._title(candidate["text"])
            requirement.text = candidate["text"]
            requirement.normalized_text = candidate["normalized_text"]
            requirement.source_refs = sorted(
                candidate["source_refs"],
                key=lambda item: json.dumps(item, sort_keys=True),
            )
            requirement.metadata_ = {
                "derivation_version": "traceability-deriver/v1"
            }
            retained.add(stable_hash)
            requirements.append(requirement)

        for stable_hash, requirement in existing.items():
            if stable_hash not in retained:
                self.db.delete(requirement)
        self.db.flush()
        return requirements

    def _replace_derived_links(
        self, task: Task, requirements: list[Requirement]
    ) -> None:
        self.db.query(RequirementLink).filter(
            RequirementLink.task_id == task.id,
            RequirementLink.source == "derived",
        ).delete(synchronize_session=False)
        self.db.flush()
        bdd_events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == task.id,
                ContextEvent.event_type == "bdd_requirement_registered",
            )
            .all()
        )
        bdd_ids = {event.id for event in bdd_events}
        for requirement in requirements:
            for source_ref in requirement.source_refs:
                source_id = source_ref.get("source_id")
                if source_ref.get("source_type") == "bdd_event" and source_id in bdd_ids:
                    self._link(
                        requirement,
                        "context_event",
                        str(source_id),
                        "source",
                        metadata={"event_type": "bdd_requirement_registered"},
                    )

        step_requirements: dict[str, set[str]] = defaultdict(set)
        plans = (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.task_id == task.id)
            .order_by(ExecutionPlan.revision, ExecutionPlan.id)
            .all()
        )
        for plan in plans:
            for plan_step in plan.steps:
                matched = [
                    requirement
                    for requirement in requirements
                    if self._matches(
                        requirement,
                        [plan_step.objective, *plan_step.acceptance_criteria],
                    )
                ]
                for requirement in matched:
                    step_requirements[plan_step.id].add(requirement.id)
                    self._link(
                        requirement,
                        "execution_plan_step",
                        plan_step.id,
                        "planned_by",
                        metadata={
                            "execution_plan_id": plan.id,
                            "revision": plan.revision,
                            "sequence": plan_step.sequence,
                            "kind": plan_step.kind,
                            "stable_key": plan_step.stable_key,
                        },
                    )
                    self._link(
                        requirement,
                        "agent",
                        plan_step.assigned_agent_name,
                        "assigned_to",
                        metadata={
                            "role": plan_step.assigned_agent_role,
                            "configuration": plan_step.agent_configuration,
                            "execution_plan_step_id": plan_step.id,
                        },
                    )
                    if plan.policy_decision is not None:
                        self._link(
                            requirement,
                            "policy_decision",
                            plan.policy_decision.id,
                            "governed_by",
                            metadata={
                                "execution_plan_id": plan.id,
                                "policy_pack": plan.policy_pack,
                                "decision": plan.policy_decision.decision,
                            },
                        )
                security = plan.security_review
                if security is not None:
                    for finding in security.findings:
                        if not isinstance(finding, dict):
                            continue
                        finding_steps = {
                            str(item) for item in finding.get("step_ids", [])
                        }
                        if finding_steps and plan_step.id not in finding_steps:
                            continue
                        finding_id = str(
                            finding.get("finding_id")
                            or hashlib.sha256(
                                json.dumps(
                                    finding, sort_keys=True, default=str
                                ).encode()
                            ).hexdigest()
                        )
                        for requirement in matched:
                            self._link(
                                requirement,
                                "security_finding",
                                finding_id,
                                "reviewed_by",
                                metadata={
                                    "execution_plan_id": plan.id,
                                    "finding": finding,
                                },
                            )
                for gate in plan.approval_gates:
                    if gate.step_ids and plan_step.id not in gate.step_ids:
                        continue
                    for requirement in matched:
                        self._link(
                            requirement,
                            "approval",
                            gate.approval_id or gate.id,
                            "gated_by",
                            metadata={
                                "execution_plan_id": plan.id,
                                "status": gate.status,
                                "action": gate.action,
                                "gate_type": gate.gate_type,
                            },
                        )

        context_events = (
            self.db.query(ContextEvent)
            .filter(ContextEvent.task_id == task.id)
            .order_by(ContextEvent.created_at, ContextEvent.id)
            .all()
        )
        for context_event in context_events:
            if context_event.event_type == "bdd_requirement_registered":
                continue
            matched = self._requirements_for_event(
                requirements, context_event, step_requirements
            )
            for requirement in matched:
                relation = (
                    "verified_by"
                    if "verification" in context_event.event_type
                    or context_event.event_type == "agent_result"
                    and self._event_is_verification(context_event)
                    else "implemented_by"
                    if context_event.event_type.startswith("repository_")
                    else "observed_in"
                )
                self._link(
                    requirement,
                    "context_event",
                    context_event.id,
                    relation,
                    metadata={"event_type": context_event.event_type},
                )
                self._event_detail_links(requirement, context_event)

        runs = (
            self.db.query(Run)
            .filter(Run.task_id == task.id)
            .order_by(Run.created_at, Run.id)
            .all()
        )
        for run in runs:
            run_steps = (
                self.db.query(RunStep)
                .filter(RunStep.run_id == run.id)
                .order_by(RunStep.sequence, RunStep.id)
                .all()
            )
            linked_run_steps: dict[str, list[Requirement]] = {}
            for run_step in run_steps:
                matched = self._requirements_for_run_step(
                    requirements, run_step, step_requirements
                )
                linked_run_steps[run_step.id] = matched
                for requirement in matched:
                    self._link(
                        requirement,
                        "run_step",
                        run_step.id,
                        "executed_by",
                        run_id=run.id,
                        metadata={
                            "sequence": run_step.sequence,
                            "name": run_step.name,
                            "status": run_step.status,
                            "retry_count": run_step.retry_count,
                        },
                    )
                    agent_name = run_step.input_.get("agent_name")
                    if isinstance(agent_name, str):
                        self._link(
                            requirement,
                            "agent",
                            agent_name,
                            "executed_by",
                            run_id=run.id,
                            metadata={
                                "framework": run_step.input_.get("framework"),
                                "run_step_id": run_step.id,
                            },
                        )
            runtime_events = (
                self.db.query(RuntimeEvent)
                .filter(RuntimeEvent.run_id == run.id)
                .order_by(RuntimeEvent.sequence, RuntimeEvent.id)
                .all()
            )
            for runtime_event in runtime_events:
                matched = linked_run_steps.get(runtime_event.step_id or "", [])
                matched = matched or self._requirements_from_payload(
                    requirements, runtime_event.payload
                )
                for requirement in matched:
                    self._link(
                        requirement,
                        "runtime_event",
                        runtime_event.id,
                        "observed_in",
                        run_id=run.id,
                        metadata={
                            "sequence": runtime_event.sequence,
                            "event_type": runtime_event.event_type,
                            "event_hash": runtime_event.event_hash,
                        },
                    )

        linked_ids = {
            link.requirement_id
            for link in self.db.query(RequirementLink)
            .filter(
                RequirementLink.task_id == task.id,
                RequirementLink.target_type == "execution_plan_step",
            )
            .all()
        }
        artifacts = (
            self.db.query(Artifact)
            .filter(Artifact.task_id == task.id)
            .order_by(Artifact.created_at, Artifact.id)
            .all()
        )
        for artifact in artifacts:
            matched = self._requirements_from_payload(
                requirements, artifact.metadata_ or {}
            )
            if not matched:
                matched = [
                    requirement
                    for requirement in requirements
                    if requirement.id in linked_ids
                ]
            for requirement in matched:
                self._link(
                    requirement,
                    "artifact",
                    artifact.id,
                    "evidenced_by",
                    run_id=(artifact.metadata_ or {}).get("run_id"),
                    metadata={
                        "artifact_type": artifact.artifact_type,
                        "path": artifact.path,
                        "content_hash": artifact.content_hash,
                    },
                )

        packs = (
            self.db.query(EvidencePack)
            .join(Run, EvidencePack.run_id == Run.id)
            .filter(Run.task_id == task.id)
            .order_by(EvidencePack.created_at, EvidencePack.id)
            .all()
        )
        for pack in packs:
            for requirement in requirements:
                self._link(
                    requirement,
                    "evidence_pack",
                    pack.id,
                    "packaged_in",
                    run_id=pack.run_id,
                    metadata={
                        "manifest_hash": pack.manifest_hash,
                        "path": pack.path,
                    },
                )
        self.db.flush()

    def _requirements_for_event(
        self,
        requirements: list[Requirement],
        event: ContextEvent,
        step_requirements: dict[str, set[str]],
    ) -> list[Requirement]:
        explicit = self._requirements_from_payload(requirements, event.payload)
        if explicit:
            return explicit
        task_contract = event.payload.get("agent_task_contract")
        if isinstance(task_contract, dict):
            texts = [
                str(task_contract.get("objective", "")),
                *[
                    str(item)
                    for item in task_contract.get("acceptance_criteria", [])
                ],
            ]
            matched = [
                requirement
                for requirement in requirements
                if self._matches(requirement, texts)
            ]
            if matched:
                return matched
            step_id = task_contract.get("step_id")
            if isinstance(step_id, str):
                return [
                    requirement
                    for requirement in requirements
                    if requirement.id in step_requirements.get(step_id, set())
                ]
        if event.event_type.startswith("repository_"):
            planned_ids = {
                requirement_id
                for requirement_ids in step_requirements.values()
                for requirement_id in requirement_ids
            }
            return [
                requirement
                for requirement in requirements
                if requirement.id in planned_ids
            ] or requirements
        return []

    def _requirements_for_run_step(
        self,
        requirements: list[Requirement],
        step: RunStep,
        step_requirements: dict[str, set[str]],
    ) -> list[Requirement]:
        execution_plan_step_id = step.input_.get("execution_plan_step_id")
        if isinstance(execution_plan_step_id, str):
            matched_ids = step_requirements.get(execution_plan_step_id, set())
            if matched_ids:
                return [
                    requirement
                    for requirement in requirements
                    if requirement.id in matched_ids
                ]
        agent_task = step.input_.get("agent_task")
        if isinstance(agent_task, dict):
            texts = [
                str(agent_task.get("objective", "")),
                *[
                    str(item)
                    for item in agent_task.get("acceptance_criteria", [])
                ],
            ]
            return [
                requirement
                for requirement in requirements
                if self._matches(requirement, texts)
            ]
        return self._requirements_from_payload(requirements, step.input_)

    def _event_detail_links(
        self, requirement: Requirement, event: ContextEvent
    ) -> None:
        payload = event.payload
        run_id = payload.get("run_id")
        changed_files = payload.get("changed_files", [])
        if isinstance(changed_files, list):
            for path in sorted(str(item) for item in changed_files if item):
                self._link(
                    requirement,
                    "changed_file",
                    path,
                    "changed_by",
                    run_id=run_id if isinstance(run_id, str) else None,
                    metadata={"context_event_id": event.id},
                )
        diff_hash = payload.get("diff_sha256")
        if event.event_type == "repository_diff_captured":
            diff_hash = diff_hash or payload.get("sha256")
        if isinstance(diff_hash, str):
            self._link(
                requirement,
                "diff",
                diff_hash,
                "implemented_by",
                run_id=run_id if isinstance(run_id, str) else None,
                metadata={"context_event_id": event.id},
            )
        for key in ("commit", "commit_sha", "target_revision"):
            commit = payload.get(key)
            if isinstance(commit, str):
                self._link(
                    requirement,
                    "commit",
                    commit,
                    "implemented_by",
                    run_id=run_id if isinstance(run_id, str) else None,
                    metadata={"context_event_id": event.id, "field": key},
                )
        command = payload.get("command")
        if isinstance(command, str):
            self._link(
                requirement,
                "test" if "verification" in event.event_type else "verification",
                hashlib.sha256(command.encode()).hexdigest(),
                "verified_by",
                run_id=run_id if isinstance(run_id, str) else None,
                metadata={
                    "context_event_id": event.id,
                    "command": command,
                    "passed": payload.get("passed"),
                    "returncode": payload.get("returncode"),
                },
            )
        result = payload.get("agent_result_contract")
        if not isinstance(result, dict):
            return
        for collection, default_type in (
            ("artifacts", "artifact"),
            ("evidence", "verification"),
        ):
            for item in result.get(collection, []):
                if not isinstance(item, dict):
                    continue
                artifact_type = str(item.get("artifact_type", default_type))
                target_type = (
                    "test"
                    if "test" in artifact_type
                    else "security_finding"
                    if "security" in artifact_type
                    else "diff"
                    if artifact_type == "diff"
                    else default_type
                )
                target_id = str(
                    item.get("sha256")
                    or item.get("uri")
                    or hashlib.sha256(
                        json.dumps(item, sort_keys=True, default=str).encode()
                    ).hexdigest()
                )
                self._link(
                    requirement,
                    target_type,
                    target_id,
                    "evidenced_by",
                    run_id=run_id if isinstance(run_id, str) else None,
                    metadata={
                        "context_event_id": event.id,
                        "artifact_type": artifact_type,
                    },
                )
        for finding in result.get("findings", []):
            if not isinstance(finding, dict):
                continue
            finding_id = str(
                finding.get("finding_id")
                or hashlib.sha256(
                    json.dumps(finding, sort_keys=True, default=str).encode()
                ).hexdigest()
            )
            self._link(
                requirement,
                "security_finding",
                finding_id,
                "reviewed_by",
                run_id=run_id if isinstance(run_id, str) else None,
                metadata={"context_event_id": event.id, "finding": finding},
            )

    def _link(
        self,
        requirement: Requirement,
        target_type: str,
        target_id: str,
        relation: str,
        *,
        run_id: str | None = None,
        source: str = "derived",
        metadata: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> RequirementLink:
        target_id = str(target_id)
        existing = (
            self.db.query(RequirementLink)
            .filter(
                RequirementLink.requirement_id == requirement.id,
                RequirementLink.target_type == target_type,
                RequirementLink.target_id == target_id,
                RequirementLink.relation == relation,
            )
            .first()
        )
        if existing is not None:
            if existing.source == "external":
                return existing
            existing.run_id = run_id
            existing.metadata_ = metadata or {}
            if source == "external":
                existing.source = source
                existing.created_by = created_by
            existing.updated_at = datetime.utcnow()
            return existing
        link_id = str(
            uuid.uuid5(
                TRACEABILITY_NAMESPACE,
                f"{requirement.id}:{target_type}:{target_id}:{relation}",
            )
        )
        link = RequirementLink(
            id=link_id,
            task_id=requirement.task_id,
            requirement_id=requirement.id,
            run_id=run_id,
            target_type=target_type,
            target_id=target_id,
            relation=relation,
            source=source,
            metadata_=metadata or {},
            created_by=created_by,
        )
        self.db.add(link)
        return link

    @staticmethod
    def normalize(value: str) -> str:
        return _SPACE.sub(" ", _NON_WORD.sub(" ", value.casefold())).strip()

    @staticmethod
    def requirement_hash(normalized_text: str) -> str:
        return hashlib.sha256(
            f"requirement/v1:{normalized_text}".encode()
        ).hexdigest()

    @staticmethod
    def _title(text: str) -> str:
        value = text.strip().rstrip(".")
        return value if len(value) <= 160 else value[:157].rstrip() + "..."

    def _matches(
        self, requirement: Requirement, values: Iterable[str]
    ) -> bool:
        requirement_text = requirement.normalized_text
        for value in values:
            normalized = self.normalize(value)
            if not normalized:
                continue
            if normalized == requirement_text:
                return True
            if len(normalized) >= 12 and (
                normalized in requirement_text or requirement_text in normalized
            ):
                return True
        return False

    @staticmethod
    def _requirements_from_payload(
        requirements: list[Requirement], payload: dict[str, Any]
    ) -> list[Requirement]:
        ids = payload.get("requirement_ids", [])
        hashes = payload.get("requirement_hashes", [])
        if isinstance(payload.get("requirement_id"), str):
            ids = [*ids, payload["requirement_id"]] if isinstance(ids, list) else [
                payload["requirement_id"]
            ]
        id_set = {str(item) for item in ids} if isinstance(ids, list) else set()
        hash_set = (
            {str(item) for item in hashes} if isinstance(hashes, list) else set()
        )
        return [
            requirement
            for requirement in requirements
            if requirement.id in id_set or requirement.stable_hash in hash_set
        ]

    @staticmethod
    def _event_is_verification(event: ContextEvent) -> bool:
        result = event.payload.get("agent_result_contract", {})
        if not isinstance(result, dict):
            return False
        return bool(result.get("evidence")) or any(
            isinstance(item, dict)
            and (
                "verification" in str(item.get("artifact_type", ""))
                or "test" in str(item.get("artifact_type", ""))
            )
            for item in result.get("artifacts", [])
        )

    @classmethod
    def _contains_secret_material(cls, value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                str(key).casefold() in _SECRET_KEYS
                or bool(_SECRET_KEY.search(str(key)))
                or cls._contains_secret_material(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(cls._contains_secret_material(item) for item in value)
        return False

    @staticmethod
    def _requirement_read(requirement: Requirement) -> RequirementV1:
        return RequirementV1(
            id=requirement.id,
            task_id=requirement.task_id,
            stable_hash=requirement.stable_hash,
            position=requirement.position,
            title=requirement.title,
            text=requirement.text,
            normalized_text=requirement.normalized_text,
            source_refs=requirement.source_refs,
            metadata=requirement.metadata_,
            created_at=requirement.created_at,
            updated_at=requirement.updated_at,
        )

    @staticmethod
    def _link_read(link: RequirementLink) -> RequirementLinkV1:
        return RequirementLinkV1(
            id=link.id,
            task_id=link.task_id,
            requirement_id=link.requirement_id,
            run_id=link.run_id,
            target_type=cast(TraceabilityTargetType, link.target_type),
            target_id=link.target_id,
            relation=link.relation,
            source=cast(RequirementLinkSource, link.source),
            metadata=link.metadata_,
            created_by=link.created_by,
            created_at=link.created_at,
            updated_at=link.updated_at,
        )

    @staticmethod
    def _coverage(
        requirements: list[RequirementV1],
        links: list[RequirementLink],
    ) -> RequirementCoverageV1:
        material_links = [link for link in links if link.relation != "source"]
        linked_by_type: dict[str, set[str]] = defaultdict(set)
        count_by_type: dict[str, int] = defaultdict(int)
        covered_ids: set[str] = set()
        evidence_ids: set[str] = set()
        for link in material_links:
            covered_ids.add(link.requirement_id)
            linked_by_type[link.target_type].add(link.requirement_id)
            count_by_type[link.target_type] += 1
            if link.target_type in _EVIDENCE_TARGETS:
                evidence_ids.add(link.requirement_id)
        total = len(requirements)
        uncovered = [
            requirement
            for requirement in requirements
            if requirement.id not in covered_ids
        ]
        return RequirementCoverageV1(
            total_requirements=total,
            covered_requirements=len(covered_ids),
            uncovered_requirements=uncovered,
            coverage_percent=round(100 * len(covered_ids) / total, 2)
            if total
            else 100.0,
            evidence_covered_requirements=len(evidence_ids),
            evidence_coverage_percent=round(100 * len(evidence_ids) / total, 2)
            if total
            else 100.0,
            linked_requirements_by_target_type={
                key: len(value) for key, value in sorted(linked_by_type.items())
            },
            link_count_by_target_type=dict(sorted(count_by_type.items())),
        )
