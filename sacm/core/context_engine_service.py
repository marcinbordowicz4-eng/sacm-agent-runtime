import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from sacm.core.application_context_service import (
    MAX_FILE_BYTES,
    ApplicationContextService,
)
from sacm.core.event_service import EventService
from sacm.infrastructure.db.models import ContextEvent, Run, Task
from sacm.schemas.application_context import (
    ContextExpansionRequest,
    ContextFileExcerpt,
    ContextNodeReference,
    ContextPackageV2,
    GraphEdge,
)

MAX_EXCERPT_CHARS = 4_000
MAX_PACKAGE_CHARS = 32_000


class ContextEngineError(ValueError):
    pass


class ContextEngineService:
    """Builds bounded, evidence-backed execution context from the application graph."""

    def __init__(self, db: Session):
        self.db = db

    def build(
        self, task_id: str, request: ContextExpansionRequest
    ) -> ContextPackageV2:
        task = self.db.get(Task, task_id)
        if task is None:
            raise ContextEngineError(f"Task {task_id} not found.")
        if request.run_id is not None:
            run = self.db.get(Run, request.run_id)
            if run is None:
                raise ContextEngineError(f"Run {request.run_id} not found.")
            if run.task_id != task_id:
                raise ContextEngineError(
                    f"Run {request.run_id} does not belong to task {task_id}."
                )
        application_context = task.application_context
        if application_context is None or request.refresh_graph:
            ApplicationContextService(self.db).build(task_id)
            self.db.refresh(task)
            application_context = task.application_context
        if application_context is None:
            raise ContextEngineError("Application context is unavailable.")

        graph = application_context.graph or {}
        nodes = {item["id"]: item for item in graph.get("nodes", [])}
        edges = graph.get("edges", [])
        reasons = self._seed_reasons(
            nodes,
            application_context.impact_analysis or {},
            request,
        )
        selected, distances, truncated = self._traverse(
            nodes,
            edges,
            reasons,
            max_depth=request.max_depth,
            max_nodes=request.max_nodes,
            role=request.role,
        )
        selected_set = set(selected)
        package_edges = [
            GraphEdge.model_validate(edge)
            for edge in edges
            if edge["source"] in selected_set and edge["target"] in selected_set
        ]
        node_references = [
            ContextNodeReference(
                node_id=node_id,
                type=nodes[node_id]["type"],
                label=nodes[node_id]["label"],
                repository=nodes[node_id]["repository"],
                path=nodes[node_id].get("path"),
                distance=distances[node_id],
                reasons=sorted(reasons.get(node_id, [])),
                metadata=nodes[node_id].get("metadata", {}),
            )
            for node_id in selected
        ]
        files = self._file_excerpts(task, node_references)
        payload = {
            "task_id": task_id,
            "run_id": request.run_id,
            "step_id": request.step_id,
            "role": request.role,
            "reason": request.reason,
            "graph_hash": application_context.graph_hash,
            "seed_node_ids": sorted(reasons),
            "nodes": [item.model_dump(mode="json") for item in node_references],
            "edges": [item.model_dump(mode="json") for item in package_edges],
            "files": [item.model_dump(mode="json") for item in files],
            "requirements": request.affected_requirements,
            "max_depth": request.max_depth,
            "max_nodes": request.max_nodes,
            "truncated": truncated,
        }
        package_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        package = ContextPackageV2.model_validate(
            {**payload, "package_hash": package_hash}
        )
        persisted_payload = package.model_dump(mode="json")
        for excerpt in persisted_payload["files"]:
            excerpt["content"] = ""
            excerpt["content_included"] = False
        EventService(self.db).save(
            task_id,
            "context_package_v2",
            persisted_payload,
        )
        return package

    def latest(
        self, task_id: str, *, run_id: str | None = None
    ) -> ContextPackageV2 | None:
        events = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == task_id,
                ContextEvent.event_type == "context_package_v2",
            )
            .order_by(ContextEvent.created_at.desc(), ContextEvent.id.desc())
            .all()
        )
        for event in events:
            if run_id is None or event.payload.get("run_id") == run_id:
                return ContextPackageV2.model_validate(event.payload)
        return None

    @staticmethod
    def _seed_reasons(
        nodes: dict[str, dict[str, Any]],
        impact: dict[str, Any],
        request: ContextExpansionRequest,
    ) -> dict[str, set[str]]:
        reasons: dict[str, set[str]] = defaultdict(set)
        signals = {
            "changed_symbol": request.changed_symbols,
            "failing_symbol": request.failing_symbols,
            "changed_file": request.changed_files,
            "failed_test": request.failed_tests,
        }
        for reason, values in signals.items():
            normalized = {
                ContextEngineService._normalize(value) for value in values if value
            }
            for node_id, node in nodes.items():
                candidates = {
                    ContextEngineService._normalize(node.get("label", "")),
                    ContextEngineService._normalize(node.get("path", "")),
                    ContextEngineService._normalize(
                        str(node.get("metadata", {}).get("qualified_name", ""))
                    ),
                }
                if any(
                    value == candidate
                    or value in candidate
                    or candidate in value
                    for value in normalized
                    for candidate in candidates
                    if candidate
                ):
                    reasons[node_id].add(reason)
        stop_words = {
            "acceptance",
            "criteria",
            "must",
            "should",
            "test",
            "tests",
            "the",
            "this",
            "that",
            "with",
        }
        requirement_terms = {
            token
            for item in request.affected_requirements
            for token in ContextEngineService._normalize(item).split()
            if len(token) >= 3 and token not in stop_words
        }
        if requirement_terms:
            for node_id, node in nodes.items():
                metadata = node.get("metadata", {})
                searchable = ContextEngineService._normalize(
                    " ".join(
                        [
                            node.get("label", ""),
                            node.get("path") or "",
                            *[
                                str(metadata.get(key) or "")
                                for key in (
                                    "qualified_name",
                                    "kind",
                                    "route",
                                    "method",
                                    "name",
                                    "declaration_type",
                                )
                            ],
                        ]
                    )
                )
                if requirement_terms & set(searchable.split()):
                    reasons[node_id].add("affected_requirement")
        if not reasons:
            for item in impact.get("impacted_nodes", [])[:12]:
                node_id = item.get("node_id")
                if node_id in nodes:
                    reasons[node_id].add("task_impact")
        return reasons

    @staticmethod
    def _traverse(
        nodes: dict[str, dict[str, Any]],
        edges: list[dict[str, str]],
        reasons: dict[str, set[str]],
        *,
        max_depth: int,
        max_nodes: int,
        role: str,
    ) -> tuple[list[str], dict[str, int], bool]:
        adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for edge in edges:
            adjacency[edge["source"]].append((edge["target"], edge["type"]))
            adjacency[edge["target"]].append((edge["source"], edge["type"]))
        priority = ContextEngineService._role_priority(role)
        queue = deque((node_id, 0) for node_id in sorted(reasons))
        distances = {node_id: 0 for node_id in reasons}
        ordered: list[str] = []
        while queue and len(ordered) < max_nodes:
            node_id, distance = queue.popleft()
            if node_id not in nodes or node_id in ordered:
                continue
            ordered.append(node_id)
            if distance >= max_depth:
                continue
            neighbors = sorted(
                adjacency[node_id],
                key=lambda item: (
                    priority.get(nodes.get(item[0], {}).get("type", ""), 50),
                    item[1],
                    item[0],
                ),
            )
            for neighbor, edge_type in neighbors:
                if neighbor not in nodes or neighbor in distances:
                    continue
                distances[neighbor] = distance + 1
                reasons[neighbor].add(f"{edge_type}:{node_id}")
                queue.append((neighbor, distance + 1))
        truncated = bool(queue) or len(distances) > len(ordered)
        return ordered, distances, truncated

    @staticmethod
    def _role_priority(role: str) -> dict[str, int]:
        common = {
            "test_symbol": 0,
            "symbol": 1,
            "api_route": 2,
            "database_schema": 3,
            "file": 4,
            "dependency_manifest": 5,
            "module": 6,
            "dependency": 7,
            "repository": 8,
        }
        if role in {"reviewer", "security"}:
            common.update(
                {"api_route": 0, "database_schema": 1, "dependency": 2}
            )
        elif role in {"tester", "verifier"}:
            common.update({"test_symbol": 0, "api_route": 1, "symbol": 2})
        return common

    @staticmethod
    def _file_excerpts(
        task: Task, nodes: list[ContextNodeReference]
    ) -> list[ContextFileExcerpt]:
        context = task.application_context
        if context is None:
            return []
        roots = {
            f"repository:{row.position:03d}": Path(row.resolved_path).resolve()
            for row in context.repositories
            if row.status == "available" and row.resolved_path
        }
        grouped: dict[tuple[str, str], list[ContextNodeReference]] = defaultdict(list)
        for node in nodes:
            if node.path and node.repository in roots:
                grouped[(node.repository, node.path)].append(node)
        excerpts: list[ContextFileExcerpt] = []
        total_chars = 0
        for (repository, relative_path), related in grouped.items():
            root = roots[repository]
            path = (root / relative_path).resolve()
            if root not in path.parents or not path.is_file():
                continue
            if any(
                node.metadata.get("content_skipped") == "oversized"
                for node in related
            ):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lines = content.splitlines()
            line_numbers = [
                int(node.metadata.get("line", 1))
                for node in related
                if node.metadata.get("line")
            ]
            start = max(1, min(line_numbers, default=1) - 8)
            end = min(
                len(lines),
                max(
                    [
                        int(node.metadata.get("end_line") or node.metadata.get("line") or 1)
                        for node in related
                    ],
                    default=min(len(lines), start + 40),
                )
                + 8,
            )
            excerpt = "\n".join(lines[start - 1 : end])[:MAX_EXCERPT_CHARS]
            if not excerpt or total_chars + len(excerpt) > MAX_PACKAGE_CHARS:
                continue
            total_chars += len(excerpt)
            excerpts.append(
                ContextFileExcerpt(
                    repository=repository,
                    path=relative_path,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    start_line=start,
                    end_line=start + excerpt.count("\n"),
                    content=excerpt,
                    node_ids=sorted(node.node_id for node in related),
                )
            )
        return excerpts

    @staticmethod
    def _normalize(value: str | None) -> str:
        value = value or ""
        return " ".join(
            "".join(
                character.lower() if character.isalnum() else " "
                for character in value
            ).split()
        )
