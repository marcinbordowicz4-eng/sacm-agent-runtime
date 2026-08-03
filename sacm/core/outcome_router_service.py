import os
import re
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.agent_registry import AgentRegistry
from sacm.core.router import RouterService
from sacm.infrastructure.db.models import (
    Agent as AgentRecord,
)
from sacm.infrastructure.db.models import (
    AgentOutcomeAnalytics,
    Run,
    Task,
)
from sacm.schemas.contracts import AgentRole
from sacm.schemas.router import RouterCandidateV1, RouterDecisionV1

_TAG_PATTERNS = {
    "python": r"\b(python|pytest|django|fastapi|flask)\b",
    "typescript": r"\b(typescript|javascript|react|node|npm|vite)\b",
    "java": r"\b(java|spring|maven|gradle)\b",
    "database": r"\b(database|sql|postgres|mysql|schema|migration)\b",
    "api": r"\b(api|endpoint|rest|graphql|grpc|contract)\b",
    "security": r"\b(security|auth|oauth|oidc|secret|vulnerability)\b",
    "infrastructure": r"\b(kubernetes|terraform|docker|deployment|infrastructure)\b",
    "testing": r"\b(test|regression|coverage|mutation|property)\b",
}

_TASK_TYPE_PATTERNS = [
    (
        "delivery",
        r"\b(release|deploy|delivery|pull request|publikac|wdroż|wydan)\w*",
    ),
    (
        "security",
        r"\b(vulnerab|exploit|penetration|security audit|podatno|bezpieczeń)\w*",
    ),
    (
        "testing",
        r"\b(add|write|fix|generate|dodaj|napisz|napraw|wygeneruj)\w*\s+"
        r"(test|tests|coverage|testy)\b",
    ),
    (
        "infrastructure",
        r"\b(kubernetes|terraform|docker|infrastructure|infra|devops)\b",
    ),
    (
        "frontend",
        r"\b(frontend|react|ui|component|screen|ekran|komponent)\w*",
    ),
    (
        "backend",
        r"\b(backend|api|endpoint|database|schema|service|bff|baza)\w*",
    ),
    (
        "implementation",
        r"\b(implement|fix|add|change|refactor|zastosuj|wdroż|napraw|dodaj|zmień)\w*",
    ),
    (
        "analysis",
        r"\b(analy[sz]|assess|review|report|architecture|oceń|ocen|"
        r"przeanaliz|raport|architektur)\w*",
    ),
]

_TASK_TYPE_AGENTS = {
    "analysis": (
        "ClaudeReasoner",
        "Architect",
        "ContextAgent",
        "OpenAIAgentsExecutor",
    ),
    "security": ("SecurityAuditor", "Reviewer", "SecurityDelivery"),
    "testing": ("TestGenerator", "CloudExecutor", "MobileE2E"),
    "infrastructure": ("InfrastructureAgent", "CloudExecutor", "CodexExecutor"),
    "frontend": ("FrontendAgent", "CodexCoder", "CodexExecutor"),
    "backend": ("BackendAgent", "CodexCoder", "CodexExecutor"),
    "implementation": ("CodexCoder", "CodexExecutor", "CloudExecutor"),
    "delivery": ("GitHubDelivery", "EASWorkflow", "SecurityDelivery"),
    "general": ("ClaudeReasoner", "ContextAgent", "Architect"),
}


class OutcomeRouterService:
    """Ranks registered agents using neural priors and real outcome history."""

    def __init__(
        self,
        db: Session,
        *,
        registry: AgentRegistry | None = None,
        neural_router: RouterService | None = None,
    ) -> None:
        self.db = db
        self.registry = registry or AgentRegistry()
        self.neural_router = neural_router or RouterService()

    def rank(
        self,
        task: Task,
        context_vector: list[float],
        belief_state: list[float],
        *,
        role: AgentRole | None = None,
        risk_level: str | None = None,
        cost_budget_usd: float | None = None,
        latency_budget_ms: int | None = None,
        previous_failure_classification: str | None = None,
        neural_result: dict[str, Any] | None = None,
    ) -> RouterDecisionV1:
        minimum_samples = int(os.getenv("SACM_ROUTER_MIN_SAMPLES", "3"))
        if minimum_samples < 1:
            raise ValueError("SACM_ROUTER_MIN_SAMPLES must be at least 1.")
        neural = neural_result or self.neural_router.route(context_vector, belief_state)
        all_role_agents = [
            agent
            for agent in self.registry.all()
            if role is None or agent.contract_role == role
        ]
        if not all_role_agents:
            raise ValueError(f"No registered agents satisfy role {role}.")
        task_text = f"{task.title}\n{task.description}"
        task_type = self.task_type(task_text)
        preferred_order = _TASK_TYPE_AGENTS[task_type]
        preferred_names = set(preferred_order)
        agents = [
            agent for agent in all_role_agents if agent.name in preferred_names
        ] or all_role_agents
        registry_agents = self.registry.all()
        index_by_name = {
            agent.name: index for index, agent in enumerate(registry_agents)
        }
        probabilities = list(neural["agent_probs"])
        metadata = {
            row.name: row
            for row in self.db.query(AgentRecord)
            .filter(AgentRecord.name.in_([agent.name for agent in agents]))
            .all()
        }
        tags = self.task_tags(task_text)
        outcomes = self._outcomes(
            [agent.name for agent in agents],
            role=role,
            organization_id=task.organization_id,
        )
        raw: list[dict[str, Any]] = []
        for agent in agents:
            index = index_by_name[agent.name]
            prior = (
                float(probabilities[index])
                if index < len(probabilities)
                else 1.0 / len(registry_agents)
            )
            rows, scope = self._scoped_rows(
                outcomes.get(agent.name, []),
                project_id=task.project_id,
                task_tags=tags,
                minimum_samples=minimum_samples,
            )
            rows = self._latest_per_run(rows)
            samples = len(rows)
            successes = sum(row.outcome == "success" for row, _, _ in rows)
            failures = sum(row.outcome == "failure" for row, _, _ in rows)
            prior_strength = 2.0
            posterior = (successes + 1.0 + prior_strength * prior) / (
                samples + 2.0 + prior_strength
            )
            confidence = min(1.0, samples / minimum_samples)
            costs = [
                float(row.estimated_cost_usd)
                for row, _, _ in rows
                if row.estimated_cost_usd is not None
            ]
            latencies = [
                float(row.latency_ms)
                for row, _, _ in rows
                if row.latency_ms is not None
            ]
            retries = [float(row.retry_count) for row, _, _ in rows]
            repeated_failures = sum(
                self._failure_classification(row.failure)
                == previous_failure_classification
                for row, _, _ in rows
                if previous_failure_classification
            )
            raw.append(
                {
                    "agent": agent,
                    "index": index,
                    "metadata": metadata.get(agent.name),
                    "prior": prior,
                    "samples": samples,
                    "successes": successes,
                    "failures": failures,
                    "posterior": posterior,
                    "confidence": confidence,
                    "average_cost": sum(costs) / len(costs) if costs else None,
                    "average_latency": (
                        sum(latencies) / len(latencies) if latencies else None
                    ),
                    "average_retries": (
                        sum(retries) / len(retries) if retries else 0.0
                    ),
                    "repeated_failures": repeated_failures,
                    "scope": scope,
                    "verification_rate": (
                        sum(
                            row.verification_count > 0 or row.test_count > 0
                            for row, _, _ in rows
                        )
                        / samples
                        if samples
                        else 0.0
                    ),
                    "capability_match": (
                        1.0 if agent.name in preferred_names else 0.0
                    ),
                }
            )
        max_cost = max(
            (item["average_cost"] or 0.0 for item in raw),
            default=0.0,
        )
        max_latency = max(
            (item["average_latency"] or 0.0 for item in raw),
            default=0.0,
        )
        candidates = [
            self._candidate(
                item,
                minimum_samples=minimum_samples,
                risk_level=risk_level,
                cost_budget_usd=cost_budget_usd,
                latency_budget_ms=latency_budget_ms,
                max_cost=max_cost,
                max_latency=max_latency,
            )
            for item in raw
        ]
        candidates.sort(key=lambda item: (-item.score, item.agent_name))
        trusted = [item for item in candidates if item.trusted_outcomes]
        if trusted:
            selected = trusted[0]
            strategy = "OUTCOME_ADAPTIVE"
            fallback_reason = None
        else:
            selected = max(
                candidates,
                key=lambda item: (
                    item.capability_match,
                    -(
                        preferred_order.index(item.agent_name)
                        if item.agent_name in preferred_order
                        else len(preferred_order)
                    ),
                    -index_by_name[item.agent_name],
                ),
            )
            strategy = "DETERMINISTIC_FALLBACK"
            fallback_reason = (
                f"No candidate has the required {minimum_samples} outcome samples; "
                f"selected by deterministic {task_type} capability match."
            )
        return RouterDecisionV1(
            task_id=task.id,
            selected_agent_name=selected.agent_name,
            selected_agent_index=index_by_name[selected.agent_name],
            strategy=strategy,
            minimum_samples=minimum_samples,
            role=role,
            risk_level=risk_level,
            task_tags=tags,
            task_type=task_type,
            fallback_reason=fallback_reason,
            candidates=candidates,
        )

    @staticmethod
    def task_tags(text: str) -> list[str]:
        return sorted(
            tag
            for tag, pattern in _TAG_PATTERNS.items()
            if re.search(pattern, text, re.IGNORECASE)
        )

    @staticmethod
    def task_type(text: str) -> str:
        for task_type, pattern in _TASK_TYPE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return task_type
        return "general"

    def _outcomes(
        self,
        names: list[str],
        *,
        role: AgentRole | None,
        organization_id: str | None,
    ) -> dict[str, list[tuple[AgentOutcomeAnalytics, str | None, str]]]:
        query = (
            self.db.query(
                AgentOutcomeAnalytics,
                Run.project_id,
                Task.title,
                Task.description,
            )
            .join(Run, Run.id == AgentOutcomeAnalytics.run_id)
            .join(Task, Task.id == Run.task_id)
            .filter(
                AgentOutcomeAnalytics.agent_name.in_(names),
                AgentOutcomeAnalytics.outcome.in_(("success", "failure")),
                Task.organization_id == organization_id,
            )
        )
        if role is not None:
            query = query.filter(AgentOutcomeAnalytics.role == role)
        grouped: dict[str, list[tuple[AgentOutcomeAnalytics, str | None, str]]] = (
            defaultdict(list)
        )
        for row, project_id, title, description in query.all():
            grouped[row.agent_name].append((row, project_id, f"{title}\n{description}"))
        return grouped

    def _scoped_rows(
        self,
        rows: list[tuple[AgentOutcomeAnalytics, str | None, str]],
        *,
        project_id: str | None,
        task_tags: list[str],
        minimum_samples: int,
    ) -> tuple[
        list[tuple[AgentOutcomeAnalytics, str | None, str]],
        str,
    ]:
        project_rows = [item for item in rows if project_id and item[1] == project_id]
        if self._distinct_run_count(project_rows) >= minimum_samples:
            return project_rows, "project"
        tag_rows = [
            item
            for item in rows
            if task_tags and set(task_tags).intersection(self.task_tags(item[2]))
        ]
        if self._distinct_run_count(tag_rows) >= minimum_samples:
            return tag_rows, "task_tags"
        if rows:
            return rows, "global"
        return [], "none"

    @staticmethod
    def _distinct_run_count(
        rows: list[tuple[AgentOutcomeAnalytics, str | None, str]],
    ) -> int:
        return len({row.run_id for row, _, _ in rows})

    @staticmethod
    def _latest_per_run(
        rows: list[tuple[AgentOutcomeAnalytics, str | None, str]],
    ) -> list[tuple[AgentOutcomeAnalytics, str | None, str]]:
        latest: dict[
            str, tuple[AgentOutcomeAnalytics, str | None, str]
        ] = {}
        for item in rows:
            row = item[0]
            current = latest.get(row.run_id)
            if current is None or (
                row.computed_at,
                row.id,
            ) > (
                current[0].computed_at,
                current[0].id,
            ):
                latest[row.run_id] = item
        return list(latest.values())

    @staticmethod
    def _candidate(
        item: dict[str, Any],
        *,
        minimum_samples: int,
        risk_level: str | None,
        cost_budget_usd: float | None,
        latency_budget_ms: int | None,
        max_cost: float,
        max_latency: float,
    ) -> RouterCandidateV1:
        confidence = float(item["confidence"])
        posterior = float(item["posterior"])
        prior = float(item["prior"])
        score = confidence * posterior + (1.0 - confidence) * prior
        capability_match = float(item["capability_match"])
        score += capability_match * 0.2
        reasons = [
            f"{item['samples']} real outcome sample(s) in {item['scope']} scope.",
            f"Posterior success proxy {posterior:.3f}; neural prior {prior:.3f}.",
            f"Capability match {capability_match:.1f}.",
        ]
        average_cost = item["average_cost"]
        if average_cost is not None:
            denominator = cost_budget_usd or max_cost
            if denominator:
                cost_penalty = min(average_cost / denominator, 2.0) * 0.08
                score -= cost_penalty
                reasons.append(f"Cost penalty {cost_penalty:.3f}.")
        average_latency = item["average_latency"]
        if average_latency is not None:
            denominator = float(latency_budget_ms or max_latency)
            if denominator:
                latency_penalty = min(average_latency / denominator, 2.0) * 0.05
                score -= latency_penalty
                reasons.append(f"Latency penalty {latency_penalty:.3f}.")
        retry_penalty = min(float(item["average_retries"]) * 0.03, 0.15)
        failure_penalty = min(float(item["repeated_failures"]) * 0.04, 0.2)
        score -= retry_penalty + failure_penalty
        if retry_penalty:
            reasons.append(f"Retry penalty {retry_penalty:.3f}.")
        if failure_penalty:
            reasons.append(f"Repeated-failure penalty {failure_penalty:.3f}.")
        if (risk_level or "").upper() in {"HIGH", "CRITICAL"}:
            verification_bonus = float(item["verification_rate"]) * 0.08
            score += verification_bonus
            reasons.append(f"High-risk verification bonus {verification_bonus:.3f}.")
        metadata = item["metadata"]
        return RouterCandidateV1(
            agent_name=item["agent"].name,
            role=item["agent"].contract_role,
            provider=metadata.provider if metadata else None,
            model_name=metadata.model_name if metadata else None,
            framework="sacm-registered",
            samples=item["samples"],
            successes=item["successes"],
            failures=item["failures"],
            neural_prior=prior,
            posterior_success_probability=posterior,
            confidence_weight=confidence,
            average_cost_usd=average_cost,
            average_latency_ms=average_latency,
            average_retries=item["average_retries"],
            score=score,
            trusted_outcomes=item["samples"] >= minimum_samples,
            data_scope=item["scope"],
            capability_match=capability_match,
            reasons=reasons,
        )

    @staticmethod
    def _failure_classification(failure: dict[str, Any] | None) -> str | None:
        return str(failure.get("classification")) if failure else None
