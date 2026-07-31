import hashlib
import json
import re
import uuid
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from sacm.core.agent_registry import AgentRegistry
from sacm.core.policy_service import PolicyService
from sacm.core.secret_broker import EnterpriseSecretBroker, SecretBroker
from sacm.core.task_intake_service import TaskIntakeService
from sacm.core.tenancy_service import ResourceAuthorizationService
from sacm.infrastructure.db.models import (
    ApplicationContext,
    ExecutionPlan,
    ExecutionPlanApprovalGate,
    ExecutionPlanPolicyDecision,
    ExecutionPlanRiskDecision,
    ExecutionPlanSecretRequirement,
    ExecutionPlanSecurityReview,
    ExecutionPlanStep,
    Task,
)
from sacm.schemas.execution_plan import (
    AgentConfigurationV1,
    ExecutionPlanPolicyRead,
    ExecutionPlanSecretsRead,
    ExecutionPlanStepV1,
    ExecutionPlanV1,
    PolicyDecisionV1,
    RiskDecisionV1,
    SecretReferenceV1,
    SecretRequestV1,
    SecurityReviewV1,
)
from sacm.schemas.task import TaskContractV1

PLANNER_VERSION = "deterministic-planner/v1"
_WORDS = re.compile(r"[a-zA-Z][a-zA-Z0-9_.:/-]{2,}")
_SENTENCE_BOUNDARY = re.compile(r"(?:\r?\n)+|(?<=[.!?])\s+")
_UUID_NAMESPACE = uuid.UUID("74d35b74-253e-4b87-8e36-d91b7f205a61")


class ExecutionPlanningError(ValueError):
    pass


class ExecutionPlanningNotFoundError(ExecutionPlanningError):
    pass


class DefinitionOfReadyError(ExecutionPlanningError):
    pass


class ApplicationContextRequiredError(ExecutionPlanningError):
    pass


class ExecutionPlanningService:
    def __init__(
        self,
        db: Session,
        *,
        agent_registry: AgentRegistry | None = None,
        policy_service: PolicyService | None = None,
        secret_broker: SecretBroker | None = None,
    ) -> None:
        self.db = db
        self.agent_registry = agent_registry or AgentRegistry()
        self.policy_service = policy_service or PolicyService(db)
        self.secret_broker = secret_broker or EnterpriseSecretBroker()

    def build(
        self,
        task_id: str,
        *,
        policy_pack: str = "default",
        actor_id: str | None = None,
    ) -> ExecutionPlanV1:
        if policy_pack not in {"default", "strict"}:
            raise ExecutionPlanningError(f"Unknown policy pack: {policy_pack}")
        task = self.db.get(Task, task_id)
        if task is None:
            raise ExecutionPlanningNotFoundError(f"Task {task_id} not found.")
        resources = ResourceAuthorizationService(self.db)
        if actor_id is not None:
            task = resources.require_task(task_id, actor_id, "tasks.write")
        elif resources._production():
            raise PermissionError("Authenticated tenant context is required.")
        contract = self._contract(task)
        readiness = TaskIntakeService.assess(contract)
        if not readiness.ready:
            missing = ", ".join(readiness.missing_fields)
            raise DefinitionOfReadyError(
                f"Task {task_id} is not Definition-of-Ready; missing: {missing}."
            )
        application_context = (
            self.db.query(ApplicationContext)
            .filter(ApplicationContext.task_id == task_id)
            .first()
        )
        if application_context is None:
            raise ApplicationContextRequiredError(
                f"Task {task_id} requires an existing application context."
            )

        secret_requests = self._secret_requests(contract)
        source_hash = self._source_hash(
            contract, application_context, policy_pack, secret_requests
        )
        existing = (
            self.db.query(ExecutionPlan)
            .filter(
                ExecutionPlan.task_id == task_id,
                ExecutionPlan.source_hash == source_hash,
            )
            .first()
        )
        if existing is not None:
            return self._read(existing)

        revision = int(
            self.db.query(func.coalesce(func.max(ExecutionPlan.revision), 0))
            .filter(ExecutionPlan.task_id == task_id)
            .scalar()
            or 0
        ) + 1
        plan_id = str(uuid.uuid5(_UUID_NAMESPACE, f"{task_id}:{source_hash}"))
        steps = self._build_steps(
            plan_id, contract, application_context, secret_requests
        )
        risk_decision = self._risk_decision(application_context, steps)
        policy_input = {
            "task_id": task_id,
            "application_context_id": application_context.id,
            "application_context_hash": application_context.graph_hash,
            "risk": risk_decision.model_dump(mode="json"),
            "steps": [
                {
                    "id": step.id,
                    "kind": step.kind,
                    "required_tools": step.required_tools,
                    "risk_tags": step.risk_tags,
                }
                for step in steps
            ],
            "secret_requirement_count": len(secret_requests),
        }
        policy_decision = PolicyDecisionV1.model_validate(
            self.policy_service.evaluate_execution_plan(
                policy_input, policy_pack=policy_pack
            )
        )
        status = (
            "BLOCKED"
            if not policy_decision.allow
            else "GATED"
            if policy_decision.requires_approval
            or policy_decision.requires_security_review
            else "READY"
        )
        now = datetime.utcnow()
        tenant_context = ResourceAuthorizationService(self.db).task_context(task)
        plan = ExecutionPlan(
            id=plan_id,
            task_id=task_id,
            organization_id=(
                tenant_context.organization_id if tenant_context else None
            ),
            project_id=tenant_context.project_id if tenant_context else None,
            tenant_attribution=(
                {
                    "schema_version": "tenant-attribution/v1",
                    "source": tenant_context.source,
                }
                if tenant_context
                else None
            ),
            application_context_id=application_context.id,
            revision=revision,
            schema_version="execution-plan/v1",
            planner_version=PLANNER_VERSION,
            source_hash=source_hash,
            status=status,
            policy_pack=policy_pack,
            created_at=now,
            updated_at=now,
        )
        self.db.add(plan)
        self.db.flush()
        self.db.add_all(
            [
                ExecutionPlanStep(
                    id=step.id,
                    execution_plan_id=plan.id,
                    sequence=step.sequence,
                    stable_key=step.stable_key,
                    schema_version=step.schema_version,
                    kind=step.kind,
                    title=step.title,
                    objective=step.objective,
                    acceptance_criteria=step.acceptance_criteria,
                    context_references=step.context_references,
                    impacted_node_ids=step.impacted_node_ids,
                    required_tools=step.required_tools,
                    risk_tags=step.risk_tags,
                    depends_on=step.depends_on,
                    assigned_agent_name=step.agent.agent_name,
                    assigned_agent_role=step.agent.role,
                    agent_configuration=step.agent.model_dump(mode="json"),
                )
                for step in steps
            ]
        )
        self.db.add(
            ExecutionPlanRiskDecision(
                execution_plan_id=plan.id,
                decision=risk_decision.model_dump(mode="json"),
            )
        )
        self.db.add(
            ExecutionPlanPolicyDecision(
                execution_plan_id=plan.id,
                policy_pack=policy_pack,
                decision=policy_decision.model_dump(mode="json"),
            )
        )

        reviewer = self._agent_configuration("security", preferred="SecurityAuditor")
        self.db.add(
            ExecutionPlanSecurityReview(
                execution_plan_id=plan.id,
                required=True,
                status="PENDING",
                reviewer_configuration=reviewer.model_dump(mode="json"),
                findings=[],
            )
        )
        for position, request in enumerate(secret_requests, start=1):
            reference = self.secret_broker.resolve(request)
            self.db.add(
                ExecutionPlanSecretRequirement(
                    execution_plan_id=plan.id,
                    position=position,
                    request=request.model_dump(mode="json"),
                    reference=reference.model_dump(mode="json"),
                )
            )
        for position, requirement in enumerate(
            policy_decision.approval_gates, start=1
        ):
            gate_id = str(
                uuid.uuid5(
                    _UUID_NAMESPACE,
                    f"{plan.id}:gate:{requirement.gate_type}:{requirement.action}",
                )
            )
            self.db.add(
                ExecutionPlanApprovalGate(
                    id=gate_id,
                    execution_plan_id=plan.id,
                    position=position,
                    gate_type=requirement.gate_type,
                    action=requirement.action,
                    reason=requirement.reason,
                    status="PENDING",
                    step_ids=requirement.step_ids,
                )
            )
        self.db.commit()
        if tenant_context and actor_id:
            from sacm.core.tenancy_service import TenancyService

            TenancyService(self.db).audit_sensitive(
                tenant_context.organization_id,
                tenant_context.project_id,
                actor_id,
                "execution_plan.build",
                "execution_plan",
                plan.id,
                "Execution plan and policy decision built.",
                {"policy_pack": policy_pack, "status": status},
            )
        persisted = self.db.get(ExecutionPlan, plan.id)
        assert persisted is not None
        from sacm.core.traceability_service import TraceabilityService

        TraceabilityService(self.db).refresh(task_id)
        return self._read(persisted)

    def get(self, task_id: str) -> ExecutionPlanV1 | None:
        plan = (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.task_id == task_id)
            .order_by(ExecutionPlan.revision.desc())
            .first()
        )
        return self._read(plan) if plan is not None else None

    def get_policy(self, task_id: str) -> ExecutionPlanPolicyRead | None:
        plan = self._latest(task_id)
        if plan is None:
            return None
        read = self._read(plan)
        return ExecutionPlanPolicyRead(
            plan_id=read.id,
            risk_decision=read.risk_decision,
            policy_decision=read.policy_decision,
            approval_gates=read.approval_gates,
        )

    def get_security_review(self, task_id: str) -> SecurityReviewV1 | None:
        plan = self._latest(task_id)
        if plan is None:
            return None
        return self._read(plan).security_review

    def get_secret_requirements(
        self, task_id: str
    ) -> ExecutionPlanSecretsRead | None:
        plan = self._latest(task_id)
        if plan is None:
            return None
        read = self._read(plan)
        return ExecutionPlanSecretsRead(
            plan_id=read.id,
            requirements=read.secret_requirements,
            references=read.secret_references,
        )

    def _latest(self, task_id: str) -> ExecutionPlan | None:
        return (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.task_id == task_id)
            .order_by(ExecutionPlan.revision.desc())
            .first()
        )

    @staticmethod
    def _contract(task: Task) -> TaskContractV1:
        if not task.task_contract:
            raise DefinitionOfReadyError(
                f"Task {task.id} has no durable TaskContractV1."
            )
        try:
            return TaskContractV1.model_validate(task.task_contract)
        except ValidationError as exc:
            raise DefinitionOfReadyError(
                f"Task {task.id} has an invalid TaskContractV1."
            ) from exc

    @staticmethod
    def _secret_requests(contract: TaskContractV1) -> list[SecretRequestV1]:
        raw = contract.metadata.get("secret_requests", [])
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ExecutionPlanningError(
                "Task metadata secret_requests must be a list of secret-request/v1 objects."
            )
        try:
            requests = [SecretRequestV1.model_validate(item) for item in raw]
        except ValidationError as exc:
            raise ExecutionPlanningError(
                "Task metadata contains an invalid secret-request/v1 object."
            ) from exc
        names = [request.name for request in requests]
        if len(names) != len(set(names)):
            raise ExecutionPlanningError("Secret request names must be unique.")
        return sorted(requests, key=lambda item: item.name)

    @staticmethod
    def _source_hash(
        contract: TaskContractV1,
        application_context: ApplicationContext,
        policy_pack: str,
        secret_requests: list[SecretRequestV1],
    ) -> str:
        payload = {
            "planner_version": PLANNER_VERSION,
            "contract": contract.model_dump(mode="json"),
            "application_context_id": application_context.id,
            "graph_hash": application_context.graph_hash,
            "impact_analysis": application_context.impact_analysis,
            "risk_analysis": application_context.risk_analysis,
            "policy_pack": policy_pack,
            "secret_requests": [
                request.model_dump(mode="json") for request in secret_requests
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _build_steps(
        self,
        plan_id: str,
        contract: TaskContractV1,
        application_context: ApplicationContext,
        secret_requests: list[SecretRequestV1],
    ) -> list[ExecutionPlanStepV1]:
        requirements = self._requirements(contract)
        steps: list[ExecutionPlanStepV1] = []
        for sequence, requirement in enumerate(requirements, start=1):
            stable_key = self._stable_key("implementation", requirement)
            node_ids, references, node_types = self._context_for_requirement(
                requirement, application_context
            )
            risk_tags = self._risk_tags(requirement, node_types)
            required_tools = ["source.read", "workspace.edit"]
            if "schema" in risk_tags:
                required_tools.append("schema.migrate")
            if "deployment" in risk_tags:
                required_tools.append("deployment.execute")
            if any(
                not request.step_keys or stable_key in request.step_keys
                for request in secret_requests
            ):
                required_tools.append("secrets.resolve")
            preferred = self._preferred_agent(requirement, node_types, risk_tags)
            agent = self._agent_configuration("coder", preferred=preferred)
            step_id = str(uuid.uuid5(_UUID_NAMESPACE, f"{plan_id}:{stable_key}"))
            steps.append(
                ExecutionPlanStepV1(
                    id=step_id,
                    sequence=sequence,
                    stable_key=stable_key,
                    kind="implementation",
                    title=self._title(requirement),
                    objective=requirement,
                    acceptance_criteria=[requirement],
                    context_references=references,
                    impacted_node_ids=node_ids,
                    required_tools=sorted(set(required_tools)),
                    risk_tags=risk_tags,
                    agent=agent,
                )
            )

        implementation_ids = [step.id for step in steps]
        verification_key = self._stable_key(
            "verification", "\n".join(contract.acceptance_criteria)
        )
        verification_id = str(
            uuid.uuid5(_UUID_NAMESPACE, f"{plan_id}:{verification_key}")
        )
        steps.append(
            ExecutionPlanStepV1(
                id=verification_id,
                sequence=len(steps) + 1,
                stable_key=verification_key,
                kind="verification",
                title="Verify acceptance criteria",
                objective="Verify every acceptance criterion against the implemented changes.",
                acceptance_criteria=contract.acceptance_criteria,
                context_references=[
                    f"application-context:{application_context.id}"
                ],
                impacted_node_ids=[],
                required_tools=["source.read", "tests.execute"],
                risk_tags=[],
                depends_on=implementation_ids,
                agent=self._agent_configuration(
                    "tester", preferred="TestGenerator"
                ),
            )
        )
        security_key = self._stable_key(
            "security_review", application_context.graph_hash
        )
        security_id = str(
            uuid.uuid5(_UUID_NAMESPACE, f"{plan_id}:{security_key}")
        )
        steps.append(
            ExecutionPlanStepV1(
                id=security_id,
                sequence=len(steps) + 1,
                stable_key=security_key,
                kind="security_review",
                title="Review execution plan security",
                objective=(
                    "Review the complete plan, privileged tools, secret references, "
                    "and application-impact risk before execution."
                ),
                acceptance_criteria=[
                    "Security findings are recorded using security-finding/v1.",
                    "The security gate is explicitly approved or changes are required.",
                ],
                context_references=[
                    f"application-context:{application_context.id}",
                    f"execution-plan:{plan_id}",
                ],
                impacted_node_ids=[],
                required_tools=["source.read", "security.scan"],
                risk_tags=["security_sensitive"],
                depends_on=[verification_id],
                agent=self._agent_configuration(
                    "security", preferred="SecurityAuditor"
                ),
            )
        )
        return steps

    @staticmethod
    def _requirements(contract: TaskContractV1) -> list[str]:
        ordered = [
            value.strip()
            for value in contract.acceptance_criteria
            if value.strip()
        ]
        ordered.extend(
            fragment.strip(" \t-*")
            for fragment in _SENTENCE_BOUNDARY.split(contract.description)
            if fragment.strip(" \t-*")
        )
        result: list[str] = []
        seen: set[str] = set()
        for requirement in ordered:
            normalized = " ".join(requirement.lower().split())
            if normalized not in seen:
                seen.add(normalized)
                result.append(requirement)
            if len(result) == 50:
                break
        if not result:
            raise DefinitionOfReadyError(
                "Task has no decomposable acceptance criteria or description."
            )
        return result

    @staticmethod
    def _context_for_requirement(
        requirement: str, application_context: ApplicationContext
    ) -> tuple[list[str], list[str], set[str]]:
        graph_nodes = {
            str(node["id"]): node
            for node in application_context.graph.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }
        impacted = application_context.impact_analysis.get("impacted_nodes", [])
        terms = {
            match.group(0).lower()
            for match in _WORDS.finditer(requirement)
            if len(match.group(0)) >= 3
        }
        matched: list[str] = []
        for item in impacted:
            node_id = str(item.get("node_id", ""))
            node = graph_nodes.get(node_id, {})
            haystack = " ".join(
                str(node.get(key, "")) for key in ("label", "path", "repository", "type")
            ).lower()
            if terms and any(term in haystack for term in terms):
                matched.append(node_id)
        if not matched:
            matched = [
                str(item.get("node_id"))
                for item in impacted[:5]
                if item.get("node_id") in graph_nodes
            ]
        matched = matched[:20]
        references = [
            f"application-context:{application_context.id}#node={node_id}"
            for node_id in matched
        ]
        node_types = {
            str(graph_nodes[node_id].get("type"))
            for node_id in matched
            if node_id in graph_nodes
        }
        return matched, references, node_types

    @staticmethod
    def _risk_tags(
        requirement: str, node_types: set[str]
    ) -> list[str]:
        text = requirement.lower()
        tags: set[str] = set()
        if "database_schema" in node_types or any(
            term in text
            for term in ("schema", "migration", "database", "table", "column")
        ):
            tags.add("schema")
        if any(
            term in text
            for term in ("deploy", "deployment", "production", "release", "rollout")
        ):
            tags.add("deployment")
        if any(
            term in text
            for term in (
                "security",
                "authentication",
                "authorization",
                "credential",
                "secret",
                "token",
                "permission",
            )
        ):
            tags.add("security_sensitive")
        return sorted(tags)

    @staticmethod
    def _preferred_agent(
        requirement: str, node_types: set[str], risk_tags: list[str]
    ) -> str:
        text = requirement.lower()
        if "deployment" in risk_tags or any(
            value in text for value in ("infrastructure", "terraform", "kubernetes")
        ):
            return "InfrastructureAgent"
        if "frontend" in text or any(
            value in text for value in ("react", "component", "browser", "ui")
        ):
            return "FrontendAgent"
        if node_types.intersection({"api_route", "database_schema"}):
            return "BackendAgent"
        return "CodexCoder"

    def _agent_configuration(
        self, role: str, *, preferred: str | None = None
    ) -> AgentConfigurationV1:
        candidates = [
            agent
            for agent in self.agent_registry.all()
            if agent.contract_role == role
        ]
        if not candidates:
            raise ExecutionPlanningError(
                f"No registered agent satisfies required role: {role}."
            )
        candidates.sort(key=lambda item: item.name)
        agent = next(
            (candidate for candidate in candidates if candidate.name == preferred),
            candidates[0],
        )
        return AgentConfigurationV1(
            runtime_kind="registered",
            agent_name=agent.name,
            role=agent.contract_role,
            implementation_ref=f"registry://{agent.name}",
            capabilities=sorted({agent.role, agent.contract_role}),
            configuration={
                "adapter": "registered-agent",
                "portable_contract_only": True,
            },
        )

    @staticmethod
    def _risk_decision(
        application_context: ApplicationContext,
        steps: list[ExecutionPlanStepV1],
    ) -> RiskDecisionV1:
        risk = application_context.risk_analysis
        tags = {tag for step in steps for tag in step.risk_tags}
        controls = ["security-review"]
        if "schema" in tags:
            controls.append("schema-change-approval")
        if "deployment" in tags:
            controls.append("deployment-approval")
        if any(
            tool in {"schema.migrate", "deployment.execute", "secrets.resolve"}
            for step in steps
            for tool in step.required_tools
        ):
            controls.append("privileged-tool-approval")
        return RiskDecisionV1.model_validate(
            {
                "score": int(risk.get("score", 100)),
                "level": str(risk.get("level", "critical")),
                "factors": list(risk.get("factors", [])),
                "required_controls": sorted(set(controls)),
            }
        )

    @staticmethod
    def _stable_key(kind: str, value: str) -> str:
        normalized = " ".join(value.lower().split())
        return f"{kind}-{hashlib.sha256(normalized.encode()).hexdigest()[:16]}"

    @staticmethod
    def _title(requirement: str) -> str:
        title = requirement.strip().rstrip(".")
        return title if len(title) <= 120 else title[:117].rstrip() + "..."

    @staticmethod
    def _read(plan: ExecutionPlan) -> ExecutionPlanV1:
        secret_requests = [
            SecretRequestV1.model_validate(item.request)
            for item in plan.secret_requirements
        ]
        secret_references = [
            SecretReferenceV1.model_validate(item.reference)
            for item in plan.secret_requirements
            if item.reference is not None
        ]
        security = plan.security_review
        return ExecutionPlanV1.model_validate(
            {
                "id": plan.id,
                "task_id": plan.task_id,
                "application_context_id": plan.application_context_id,
                "revision": plan.revision,
                "planner_version": plan.planner_version,
                "source_hash": plan.source_hash,
                "status": plan.status,
                "policy_pack": plan.policy_pack,
                "steps": [
                    {
                        "id": step.id,
                        "sequence": step.sequence,
                        "stable_key": step.stable_key,
                        "kind": step.kind,
                        "title": step.title,
                        "objective": step.objective,
                        "acceptance_criteria": step.acceptance_criteria,
                        "context_references": step.context_references,
                        "impacted_node_ids": step.impacted_node_ids,
                        "required_tools": step.required_tools,
                        "risk_tags": step.risk_tags,
                        "depends_on": step.depends_on,
                        "agent": step.agent_configuration,
                    }
                    for step in plan.steps
                ],
                "risk_decision": plan.risk_decision.decision,
                "policy_decision": plan.policy_decision.decision,
                "security_review": {
                    "required": security.required,
                    "status": security.status,
                    "reviewer": security.reviewer_configuration,
                    "findings": security.findings,
                    "reviewed_at": security.reviewed_at,
                    "reviewed_by": security.reviewed_by,
                },
                "secret_requirements": secret_requests,
                "secret_references": secret_references,
                "approval_gates": [
                    {
                        "id": gate.id,
                        "gate_type": gate.gate_type,
                        "action": gate.action,
                        "reason": gate.reason,
                        "status": gate.status,
                        "step_ids": gate.step_ids,
                        "approval_id": gate.approval_id,
                    }
                    for gate in plan.approval_gates
                ],
                "created_at": plan.created_at,
                "updated_at": plan.updated_at,
            }
        )
