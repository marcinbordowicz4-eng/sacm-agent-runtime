from typing import Any

from sacm.agents.base import Agent
from sacm.core.repository_config import load_repository_config
from sacm.infrastructure.db.models import ContextEvent, MemoryChunk, Task
from sacm.schemas.application_context import ContextPackageV2
from sacm.schemas.context import AgentContext
from sacm.schemas.contracts import AgentTaskV1


class ContextCompiler:
    def __init__(self, token_budget: int = 12000):
        self.token_budget = token_budget

    def compile(
        self,
        task: Task,
        agent: Agent,
        history: list[ContextEvent],
        memory: list[MemoryChunk],
        context_package: ContextPackageV2 | None = None,
    ) -> AgentContext:
        repository_config = load_repository_config(task.target_repo_path)
        task_text = self._trim(task.description, self.token_budget)
        goal = self._trim(f"Complete task: {task.title}", self.token_budget)
        constraints = [
            f"Agent role: {agent.role}",
            f"Token budget: {self.token_budget}",
            *(repository_config.constraints if repository_config else []),
        ]
        remaining = self.token_budget - self._estimate_tokens(task_text)
        remaining -= self._estimate_tokens(goal)
        remaining -= sum(self._estimate_tokens(item) for item in constraints)

        relevant_memory, remaining = self._fit(
            [chunk.content for chunk in memory[:8]], remaining
        )
        previous_findings, remaining = self._fit(
            [
            event.payload.get("summary", "")
            for event in history[:5]
            if event.event_type == "agent_result"
            ],
            remaining,
        )
        files: dict[str, str] = {}
        package_payload: dict[str, Any] | None = None
        if context_package:
            package_budget = min(
                max(0, remaining // 3),
                max(0, self.token_budget // 4),
            )
            if package_budget >= 64:
                package_payload = self._compact_package(
                    context_package,
                    max_tokens=package_budget,
                )
                remaining -= self._estimate_tokens(str(package_payload))
            package_constraint = (
                "Use only context-package/v2 evidence "
                f"{context_package.package_hash} for graph-based assumptions."
            )
            constraint_tokens = self._estimate_tokens(package_constraint)
            if remaining >= constraint_tokens:
                constraints.append(package_constraint)
                remaining -= constraint_tokens
            for excerpt in context_package.files:
                if remaining <= 0:
                    break
                key = f"{excerpt.repository}:{excerpt.path}:{excerpt.start_line}"
                content = self._trim(excerpt.content, remaining)
                files[key] = content
                remaining -= self._estimate_tokens(content)
        return AgentContext(
            task_id=task.id,
            task=task_text,
            goal=goal,
            current_state=task.status,
            target_repo_path=task.target_repo_path,
            relevant_memory=relevant_memory,
            files=files,
            constraints=constraints,
            previous_findings=previous_findings,
            test_command=(
                repository_config.commands.test if repository_config else None
            ),
            build_command=(
                repository_config.commands.build if repository_config else None
            ),
            token_budget=self.token_budget,
            context_package=package_payload,
        )

    def compile_v1(
        self,
        *,
        run_id: str,
        step_id: str,
        agent: Agent,
        context: AgentContext,
    ) -> AgentTaskV1:
        return AgentTaskV1(
            run_id=run_id,
            step_id=step_id,
            role=agent.contract_role,
            objective=context.goal,
            acceptance_criteria=context.constraints,
            context_references=context.relevant_memory + context.previous_findings,
            token_budget=context.token_budget,
            timeout_seconds=300,
            execution_context=context.model_dump(),
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    @staticmethod
    def _trim(text: str, budget: int) -> str:
        return text[: max(0, budget) * 4]

    def _fit(self, values: list[str], remaining: int) -> tuple[list[str], int]:
        fitted: list[str] = []
        for value in values:
            if not value or remaining <= 0:
                continue
            trimmed = self._trim(value, remaining)
            fitted.append(trimmed)
            remaining -= self._estimate_tokens(trimmed)
        return fitted, remaining

    def _compact_package(
        self, package: ContextPackageV2, *, max_tokens: int
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": package.schema_version,
            "package_hash": package.package_hash,
            "graph_hash": package.graph_hash,
            "reason": package.reason,
            "seed_node_ids": [],
            "requirements": [],
            "nodes": [],
            "edges": [],
            "files": [],
            "truncated": package.truncated,
        }
        for seed in package.seed_node_ids[:12]:
            seed_value = seed[:200]
            if self._estimate_tokens(
                str(
                    {
                        **payload,
                        "seed_node_ids": [
                            *payload["seed_node_ids"],
                            seed_value,
                        ],
                    }
                )
            ) > max_tokens:
                break
            payload["seed_node_ids"].append(seed_value)
        for requirement in package.requirements[:12]:
            requirement_value = requirement[:200]
            if self._estimate_tokens(
                str(
                    {
                        **payload,
                        "requirements": [
                            *payload["requirements"],
                            requirement_value,
                        ],
                    }
                )
            ) > max_tokens:
                break
            payload["requirements"].append(requirement_value)
        for node in package.nodes:
            node_payload: dict[str, Any] = {
                "node_id": node.node_id,
                "type": node.type,
                "label": node.label,
                "path": node.path,
                "distance": node.distance,
                "reasons": node.reasons[:4],
                "line": node.metadata.get("line"),
                "end_line": node.metadata.get("end_line"),
                "kind": node.metadata.get("kind"),
            }
            if (
                self._estimate_tokens(
                    str(
                        {
                            **payload,
                            "nodes": [*payload["nodes"], node_payload],
                        }
                    )
                )
                > max_tokens
            ):
                break
            payload["nodes"].append(node_payload)
        included = {node["node_id"] for node in payload["nodes"]}
        for edge in package.edges:
            if edge.source not in included or edge.target not in included:
                continue
            edge_payload = edge.model_dump(mode="json")
            if (
                self._estimate_tokens(
                    str(
                        {
                            **payload,
                            "edges": [*payload["edges"], edge_payload],
                        }
                    )
                )
                > max_tokens
            ):
                break
            payload["edges"].append(edge_payload)
        for excerpt in package.files:
            file_payload = {
                "repository": excerpt.repository,
                "path": excerpt.path,
                "content_hash": excerpt.content_hash,
                "start_line": excerpt.start_line,
                "end_line": excerpt.end_line,
                "node_ids": excerpt.node_ids,
            }
            if (
                self._estimate_tokens(
                    str(
                        {
                            **payload,
                            "files": [*payload["files"], file_payload],
                        }
                    )
                )
                > max_tokens
            ):
                break
            payload["files"].append(file_payload)
        return payload
