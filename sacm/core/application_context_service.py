import ast
import hashlib
import json
import os
import posixpath
import re
import stat
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from sacm.adapters.code_intelligence import ScipJsonAdapter
from sacm.adapters.repository_adapter import RepositoryAdapter, RepositoryError
from sacm.infrastructure.db.models import (
    ApplicationContext,
    ApplicationContextRepository,
    ContextEvent,
    Project,
    Task,
)
from sacm.schemas.application_context import (
    ApplicationContextRead,
    ImpactRiskRead,
    RepositoryContextRead,
)
from sacm.schemas.task import RepositoryReference, TaskContractV1

MAX_REPOSITORIES = 20
MAX_FILES_PER_REPOSITORY = 5_000
MAX_GRAPH_NODES = 20_000
MAX_GRAPH_EDGES = 40_000
MAX_FILE_BYTES = 1_000_000
MAX_IMPACT_NODES = 100
SCANNER_VERSION = "deterministic-scanner/v2.1"

EXCLUDED_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "bin",
    "build",
    "coverage",
    "dist",
    "generated",
    "node_modules",
    "obj",
    "out",
    "target",
    "vendor",
    "venv",
}
TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".ex",
    ".exs",
    ".go",
    ".gradle",
    ".graphql",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".php",
    ".prisma",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".xml",
    ".yaml",
    ".yml",
}
MANIFEST_NAMES = {
    "Cargo.toml",
    "Gemfile",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "go.mod",
    "package.json",
    "pom.xml",
    "pyproject.toml",
}
STOP_WORDS = {
    "acceptance",
    "add",
    "after",
    "against",
    "all",
    "and",
    "application",
    "are",
    "build",
    "change",
    "criteria",
    "description",
    "for",
    "from",
    "have",
    "implement",
    "into",
    "must",
    "new",
    "not",
    "phase",
    "repository",
    "repositories",
    "should",
    "task",
    "that",
    "the",
    "this",
    "through",
    "use",
    "using",
    "where",
    "with",
    "file",
    "files",
    "module",
    "modules",
}
HIGH_RISK_TERMS = {
    "auth",
    "authentication",
    "authorization",
    "billing",
    "credential",
    "delete",
    "migration",
    "payment",
    "permission",
    "production",
    "schema",
    "secret",
    "security",
}


class ApplicationContextError(ValueError):
    pass


class ApplicationContextNotFoundError(ApplicationContextError):
    pass


@dataclass
class GraphBuilder:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: set[tuple[str, str, str]] = field(default_factory=set)
    truncated: bool = False

    def add_node(self, node: dict[str, Any]) -> bool:
        node_id = node["id"]
        if node_id in self.nodes:
            return True
        if len(self.nodes) >= MAX_GRAPH_NODES:
            self.truncated = True
            return False
        self.nodes[node_id] = node
        return True

    def add_edge(self, source: str, target: str, edge_type: str) -> None:
        if source not in self.nodes or target not in self.nodes:
            return
        if len(self.edges) >= MAX_GRAPH_EDGES:
            self.truncated = True
            return
        self.edges.add((source, target, edge_type))

    def dump(self) -> dict[str, Any]:
        return {
            "nodes": [self.nodes[key] for key in sorted(self.nodes)],
            "edges": [
                {"source": source, "target": target, "type": edge_type}
                for source, target, edge_type in sorted(self.edges)
            ],
            "truncated": self.truncated,
            "limits": {
                "max_edges": MAX_GRAPH_EDGES,
                "max_file_bytes": MAX_FILE_BYTES,
                "max_files_per_repository": MAX_FILES_PER_REPOSITORY,
                "max_nodes": MAX_GRAPH_NODES,
                "max_repositories": MAX_REPOSITORIES,
            },
        }


@dataclass
class ScanResult:
    file_count: int = 0
    skipped_file_count: int = 0
    oversized_file_count: int = 0
    unreadable_file_count: int = 0
    module_count: int = 0
    dependency_count: int = 0
    api_route_count: int = 0
    schema_count: int = 0
    scan_errors: list[str] = field(default_factory=list)
    code_intelligence: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def metadata(self) -> dict[str, Any]:
        return {
            "api_route_count": self.api_route_count,
            "code_intelligence": self.code_intelligence,
            "dependency_count": self.dependency_count,
            "excluded_directories": sorted(EXCLUDED_DIRECTORIES),
            "module_count": self.module_count,
            "oversized_file_count": self.oversized_file_count,
            "scan_errors": sorted(self.scan_errors),
            "schema_count": self.schema_count,
            "truncated": self.truncated,
            "unreadable_file_count": self.unreadable_file_count,
        }


class ApplicationContextService:
    def __init__(self, db: Session):
        self.db = db

    def build(self, task_id: str) -> ApplicationContextRead:
        task = self.db.get(Task, task_id)
        if task is None:
            raise ApplicationContextNotFoundError(f"Task {task_id} not found.")
        contract = self._task_contract(task)
        graph = GraphBuilder()
        repository_rows: list[ApplicationContextRepository] = []

        for position, reference in enumerate(contract.repositories):
            repository_id = f"repository:{position:03d}"
            row = ApplicationContextRepository(
                position=position,
                full_name=reference.full_name,
                requested_path=reference.path,
                base_revision=reference.base_revision,
                status="unavailable",
            )
            try:
                adapter = self._resolve_repository(reference)
            except RepositoryError as exc:
                row.error_code = "repository_path_invalid"
                row.error_message = str(exc)
                graph.add_node(
                    self._repository_node(repository_id, reference, "unavailable")
                )
            except ApplicationContextError as exc:
                row.error_code = "repository_unavailable"
                row.error_message = str(exc)
                graph.add_node(
                    self._repository_node(repository_id, reference, "unavailable")
                )
            else:
                row.status = "available"
                row.resolved_path = str(adapter.repo_path)
                graph.add_node(self._repository_node(repository_id, reference, "available"))
                result = self._scan_repository(adapter, repository_id, graph)
                row.file_count = result.file_count
                row.skipped_file_count = result.skipped_file_count
                row.scan_metadata = result.metadata()
                if result.scan_errors:
                    row.status = "unavailable"
                    row.error_code = "repository_scan_failed"
                    row.error_message = "; ".join(sorted(result.scan_errors))
                    graph.nodes[repository_id]["metadata"]["status"] = "unavailable"
            repository_rows.append(row)

        graph_data = graph.dump()
        impact = self._analyze_impact(contract, graph_data)
        risk = self._analyze_risk(contract, repository_rows, graph_data, impact)
        graph_hash = hashlib.sha256(
            json.dumps(graph_data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        available_count = sum(row.status == "available" for row in repository_rows)
        status = (
            "complete"
            if (
                repository_rows
                and available_count == len(repository_rows)
                and not graph.truncated
            )
            else "partial"
            if available_count
            else "unavailable"
        )

        context = (
            self.db.query(ApplicationContext)
            .filter(ApplicationContext.task_id == task.id)
            .first()
        )
        if context is None:
            context = ApplicationContext(
                task_id=task.id,
                status=status,
                scanner_version=SCANNER_VERSION,
                graph=graph_data,
                graph_hash=graph_hash,
                impact_analysis=impact,
                risk_analysis=risk,
            )
            self.db.add(context)
            self.db.flush()
        else:
            context.status = status
            context.scanner_version = SCANNER_VERSION
            context.graph = graph_data
            context.graph_hash = graph_hash
            context.impact_analysis = impact
            context.risk_analysis = risk
            context.repositories.clear()
            self.db.flush()
        for row in repository_rows:
            context.repositories.append(row)
        self.db.add(
            ContextEvent(
                task_id=task.id,
                organization_id=task.organization_id,
                project_id=task.project_id,
                tenant_attribution=task.tenant_attribution,
                data_region=task.data_region,
                data_classification=task.data_classification,
                event_type="application_context_built",
                payload={
                    "application_context_id": context.id,
                    "graph_hash": graph_hash,
                    "repository_count": len(repository_rows),
                    "status": status,
                },
            )
        )
        self.db.commit()
        self.db.refresh(context)
        return self._read(context)

    def get(self, task_id: str) -> ApplicationContextRead | None:
        context = (
            self.db.query(ApplicationContext)
            .filter(ApplicationContext.task_id == task_id)
            .first()
        )
        return self._read(context) if context else None

    def get_impact_risk(self, task_id: str) -> ImpactRiskRead | None:
        context = (
            self.db.query(ApplicationContext)
            .filter(ApplicationContext.task_id == task_id)
            .first()
        )
        if context is None:
            return None
        return ImpactRiskRead.model_validate(
            {
                "task_id": task_id,
                "application_context_id": context.id,
                "graph_hash": context.graph_hash,
                "impact_analysis": context.impact_analysis,
                "risk_analysis": context.risk_analysis,
            }
        )

    def _task_contract(self, task: Task) -> TaskContractV1:
        if task.task_contract:
            try:
                return TaskContractV1.model_validate(task.task_contract)
            except ValueError as exc:
                raise ApplicationContextError(
                    f"Task {task.id} has an invalid TaskContractV1."
                ) from exc
        repositories = (
            [RepositoryReference(path=task.target_repo_path)]
            if task.target_repo_path
            else []
        )
        return TaskContractV1(
            connector_type="generic",
            external_id=task.id,
            title=task.title,
            description=task.description,
            repositories=repositories,
        )

    def _resolve_repository(
        self, reference: RepositoryReference
    ) -> RepositoryAdapter:
        path = reference.path
        if path is None and reference.full_name:
            project = (
                self.db.query(Project)
                .filter(Project.repository_full_name == reference.full_name)
                .order_by(Project.id)
                .first()
            )
            path = project.repository_path if project else None
            if path is None:
                try:
                    mappings = json.loads(
                        os.getenv("SACM_GITHUB_REPOSITORIES_JSON", "{}")
                    )
                except json.JSONDecodeError as exc:
                    raise ApplicationContextError(
                        "SACM_GITHUB_REPOSITORIES_JSON is invalid JSON."
                    ) from exc
                mapped = (
                    mappings.get(reference.full_name)
                    if isinstance(mappings, dict)
                    else None
                )
                path = mapped if isinstance(mapped, str) and mapped else None
        if path is None:
            identity = reference.full_name or "<missing repository reference>"
            raise ApplicationContextError(
                f"Repository {identity} has no available local path mapping."
            )
        return RepositoryAdapter(path)

    @staticmethod
    def _repository_node(
        repository_id: str, reference: RepositoryReference, status: str
    ) -> dict[str, Any]:
        label = reference.full_name or reference.path or repository_id
        return {
            "id": repository_id,
            "type": "repository",
            "repository": repository_id,
            "label": label,
            "path": None,
            "metadata": {
                "base_revision": reference.base_revision,
                "full_name": reference.full_name,
                "status": status,
            },
        }

    def _scan_repository(
        self, adapter: RepositoryAdapter, repository_id: str, graph: GraphBuilder
    ) -> ScanResult:
        result = ScanResult()
        module_ids: set[str] = set()
        file_ids: dict[str, str] = {}
        pending_imports: list[tuple[str, str, str]] = []
        pending_calls: list[tuple[str, str]] = []
        symbol_ids: dict[str, list[str]] = defaultdict(list)
        file_symbol_ids: dict[tuple[str, str], str] = {}
        route_symbols: list[tuple[str, str, str]] = []
        schema_symbols: list[tuple[str, str, str]] = []
        paths: list[str] = []
        for root, directories, filenames in os.walk(
            adapter.repo_path,
            onerror=lambda error: result.scan_errors.append(str(error)),
        ):
            directories[:] = sorted(
                directory
                for directory in directories
                if directory not in EXCLUDED_DIRECTORIES
                and not directory.startswith(".")
                and not (Path(root) / directory).is_symlink()
            )
            for filename in sorted(filenames):
                if len(paths) >= MAX_FILES_PER_REPOSITORY:
                    result.truncated = True
                    break
                relative = (Path(root) / filename).relative_to(adapter.repo_path)
                paths.append(relative.as_posix())
            if result.truncated:
                break

        for relative_path in paths:
            full_path = adapter.repo_path / relative_path
            try:
                resolved_path = full_path.resolve()
                if adapter.repo_path not in resolved_path.parents:
                    result.skipped_file_count += 1
                    result.unreadable_file_count += 1
                    continue
                file_stat = resolved_path.stat()
                if not stat.S_ISREG(file_stat.st_mode):
                    result.skipped_file_count += 1
                    result.unreadable_file_count += 1
                    continue
                size = file_stat.st_size
            except OSError:
                result.skipped_file_count += 1
                result.unreadable_file_count += 1
                continue
            file_id = f"{repository_id}:file:{relative_path}"
            file_ids[relative_path] = file_id
            if not graph.add_node(
                {
                    "id": file_id,
                    "type": (
                        "dependency_manifest"
                        if self._is_manifest(relative_path)
                        else "file"
                    ),
                    "repository": repository_id,
                    "label": Path(relative_path).name,
                    "path": relative_path,
                    "metadata": {"size_bytes": size},
                }
            ):
                result.truncated = True
                break
            result.file_count += 1
            self._link_module(
                repository_id,
                relative_path,
                file_id,
                graph,
                module_ids,
            )
            if size > MAX_FILE_BYTES:
                result.skipped_file_count += 1
                result.oversized_file_count += 1
                graph.nodes[file_id]["metadata"]["content_skipped"] = "oversized"
                continue
            if not self._is_text_candidate(relative_path):
                result.skipped_file_count += 1
                graph.nodes[file_id]["metadata"]["content_skipped"] = "non_text"
                continue
            try:
                content = adapter.read_file(relative_path)
            except OSError:
                result.skipped_file_count += 1
                result.unreadable_file_count += 1
                graph.nodes[file_id]["metadata"]["content_skipped"] = "unreadable"
                continue
            if "\x00" in content:
                result.skipped_file_count += 1
                graph.nodes[file_id]["metadata"]["content_skipped"] = "binary"
                continue
            dependencies = self._extract_dependencies(relative_path, content)
            for ecosystem, name, scope in dependencies:
                dependency_id = f"{repository_id}:dependency:{ecosystem}:{name}"
                if graph.add_node(
                    {
                        "id": dependency_id,
                        "type": "dependency",
                        "repository": repository_id,
                        "label": name,
                        "path": relative_path,
                        "metadata": {"ecosystem": ecosystem, "scope": scope},
                    }
                ):
                    graph.add_edge(file_id, dependency_id, "declares_dependency")
            result.dependency_count += len(dependencies)

            routes = self._extract_routes(relative_path, content)
            for index, route in enumerate(routes):
                route_id = (
                    f"{repository_id}:api_route:{relative_path}:"
                    f"{route['line']:06d}:{index:03d}"
                )
                if graph.add_node(
                    {
                        "id": route_id,
                        "type": "api_route",
                        "repository": repository_id,
                        "label": f"{route['method']} {route['route']}",
                        "path": relative_path,
                        "metadata": route,
                    }
                ):
                    graph.add_edge(file_id, route_id, "declares_api_route")
                    if route["handler"]:
                        route_symbols.append(
                            (route_id, relative_path, route["handler"])
                        )
            result.api_route_count += len(routes)

            schemas = self._extract_schemas(relative_path, content)
            for index, schema in enumerate(schemas):
                schema_id = (
                    f"{repository_id}:database_schema:{relative_path}:"
                    f"{schema['line']:06d}:{index:03d}"
                )
                if graph.add_node(
                    {
                        "id": schema_id,
                        "type": "database_schema",
                        "repository": repository_id,
                        "label": schema["name"],
                        "path": relative_path,
                        "metadata": schema,
                    }
                ):
                    graph.add_edge(file_id, schema_id, "declares_database_schema")
                    schema_symbols.append(
                        (schema_id, relative_path, schema["name"])
                    )
            result.schema_count += len(schemas)
            for index, symbol in enumerate(
                self._extract_symbols(relative_path, content)
            ):
                symbol_id = (
                    f"{repository_id}:symbol:{relative_path}:"
                    f"{symbol['line']:06d}:{index:03d}"
                )
                if not graph.add_node(
                    {
                        "id": symbol_id,
                        "type": "test_symbol" if symbol["is_test"] else "symbol",
                        "repository": repository_id,
                        "label": symbol["name"],
                        "path": relative_path,
                        "metadata": {
                            key: value
                            for key, value in symbol.items()
                            if key != "calls"
                        },
                    }
                ):
                    result.truncated = True
                    continue
                graph.add_edge(file_id, symbol_id, "declares_symbol")
                symbol_ids[symbol["name"]].append(symbol_id)
                file_symbol_ids[(relative_path, symbol["name"])] = symbol_id
                pending_calls.extend(
                    (symbol_id, called) for called in symbol["calls"]
                )
            pending_imports.extend(
                (file_id, kind, imported)
                for kind, imported in self._extract_internal_imports(
                    relative_path, content
                )
            )

        self._link_imports(repository_id, pending_imports, file_ids, graph)
        self._link_symbols(
            pending_calls,
            symbol_ids,
            graph,
        )
        for contract_id, path, symbol_name in route_symbols:
            linked_symbol_id = file_symbol_ids.get((path, symbol_name))
            if linked_symbol_id:
                graph.add_edge(contract_id, linked_symbol_id, "implemented_by")
        for contract_id, path, symbol_name in schema_symbols:
            linked_symbol_id = file_symbol_ids.get((path, symbol_name))
            if linked_symbol_id:
                graph.add_edge(contract_id, linked_symbol_id, "represented_by")
        result.code_intelligence = ScipJsonAdapter().merge(
            adapter.repo_path,
            repository_id,
            graph,
            file_ids,
        ).metadata()
        result.module_count = len(module_ids)
        result.truncated = result.truncated or graph.truncated
        return result

    @staticmethod
    def _extract_symbols(path: str, content: str) -> list[dict[str, Any]]:
        suffix = Path(path).suffix.lower()
        symbols: list[dict[str, Any]] = []
        test_path = bool(
            re.search(r"(^|/)(tests?|__tests__)(/|$)", path)
            or re.search(r"(?:^|[_.-])(test|spec)(?:[_.-]|$)", Path(path).name)
        )
        if suffix == ".py":
            try:
                tree = ast.parse(content)
            except SyntaxError:
                return []
            parents: list[str] = []

            def visit(body: list[ast.stmt]) -> None:
                for node in body:
                    if isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                    ):
                        qualified = ".".join([*parents, node.name])
                        calls = ApplicationContextService._owned_python_calls(node)
                        symbols.append(
                            {
                                "name": node.name,
                                "qualified_name": qualified,
                                "kind": (
                                    "class"
                                    if isinstance(node, ast.ClassDef)
                                    else "function"
                                ),
                                "line": node.lineno,
                                "end_line": getattr(node, "end_lineno", node.lineno),
                                "is_test": test_path
                                or node.name.startswith(("test_", "Test")),
                                "calls": calls,
                            }
                        )
                        parents.append(node.name)
                        visit(node.body)
                        parents.pop()
            visit(tree.body)
            return symbols

        patterns: tuple[tuple[str, str], ...]
        if suffix in {".js", ".jsx", ".ts", ".tsx"}:
            patterns = (
                (
                    "function",
                    r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
                ),
                (
                    "function",
                    r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)"
                    r"\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
                ),
                (
                    "class",
                    r"(?m)^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)",
                ),
            )
        elif suffix in {".java", ".kt", ".kts", ".cs"}:
            patterns = (
                (
                    "class",
                    r"(?m)^\s*(?:public\s+|private\s+|protected\s+|internal\s+)?"
                    r"(?:class|interface|record|object)\s+(\w+)",
                ),
                (
                    "function",
                    r"(?m)^\s*(?:public\s+|private\s+|protected\s+|static\s+|"
                    r"final\s+|suspend\s+|override\s+)*[\w<>\[\],?.]+\s+(\w+)\s*\([^;]*\)\s*\{",
                ),
            )
        elif suffix == ".go":
            patterns = (
                ("function", r"(?m)^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\("),
                ("class", r"(?m)^\s*type\s+(\w+)\s+struct\s*\{"),
            )
        else:
            return []
        declarations: list[tuple[int, int, str, str]] = []
        seen: set[tuple[int, str]] = set()
        for kind, pattern in patterns:
            for match in re.finditer(pattern, content):
                name = match.group(1)
                line = content.count("\n", 0, match.start()) + 1
                if (line, name) in seen:
                    continue
                seen.add((line, name))
                declarations.append((match.start(), match.end(), name, kind))
        declarations.sort()
        for index, (start_offset, body_offset, name, kind) in enumerate(declarations):
            end_offset = (
                declarations[index + 1][0]
                if index + 1 < len(declarations)
                else len(content)
            )
            body = content[body_offset:end_offset]
            calls = sorted(
                set(
                    re.findall(
                        r"\b([A-Za-z_$][\w$]*)\s*\(",
                        body,
                    )
                )
                - {
                    "if",
                    "for",
                    "while",
                    "switch",
                    "catch",
                    "return",
                    "new",
                }
            )
            line = content.count("\n", 0, start_offset) + 1
            end_line = content.count("\n", 0, end_offset) + 1
            symbols.append(
                {
                    "name": name,
                    "qualified_name": name,
                    "kind": kind,
                    "line": line,
                    "end_line": max(line, end_line),
                    "is_test": test_path
                    or name.lower().startswith(("test", "should")),
                    "calls": calls[:100],
                }
            )
        return sorted(symbols, key=lambda item: (item["line"], item["name"]))

    @staticmethod
    def _call_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def _owned_python_calls(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> list[str]:
        calls: set[str] = set()

        class CallVisitor(ast.NodeVisitor):
            def visit_Call(self, child: ast.Call) -> None:
                name = ApplicationContextService._call_name(child.func)
                if name:
                    calls.add(name)
                self.generic_visit(child)

            def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
                if child is node:
                    self.generic_visit(child)

            def visit_AsyncFunctionDef(self, child: ast.AsyncFunctionDef) -> None:
                if child is node:
                    self.generic_visit(child)

            def visit_ClassDef(self, child: ast.ClassDef) -> None:
                if child is node:
                    self.generic_visit(child)

        CallVisitor().visit(node)
        return sorted(calls)

    @staticmethod
    def _link_symbols(
        calls: list[tuple[str, str]],
        symbol_ids: dict[str, list[str]],
        graph: GraphBuilder,
    ) -> None:
        for source, name in sorted(calls):
            targets = symbol_ids.get(name, [])
            if len(targets) != 1 or targets[0] == source:
                continue
            target = targets[0]
            source_node = graph.nodes.get(source, {})
            graph.add_edge(
                source,
                target,
                "tests" if source_node.get("type") == "test_symbol" else "calls",
            )

    @staticmethod
    def _link_module(
        repository_id: str,
        relative_path: str,
        file_id: str,
        graph: GraphBuilder,
        module_ids: set[str],
    ) -> None:
        parent = Path(relative_path).parent.as_posix()
        if parent == ".":
            graph.add_edge(repository_id, file_id, "contains")
            return
        module_id = f"{repository_id}:module:{parent}"
        if graph.add_node(
            {
                "id": module_id,
                "type": "module",
                "repository": repository_id,
                "label": Path(parent).name,
                "path": parent,
                "metadata": {},
            }
        ):
            module_ids.add(module_id)
            graph.add_edge(repository_id, module_id, "contains")
            graph.add_edge(module_id, file_id, "contains")

    @staticmethod
    def _is_manifest(path: str) -> bool:
        name = Path(path).name
        return name in MANIFEST_NAMES or name.startswith("requirements")

    @classmethod
    def _is_text_candidate(cls, path: str) -> bool:
        return cls._is_manifest(path) or Path(path).suffix.lower() in TEXT_SUFFIXES

    @staticmethod
    def _extract_dependencies(
        path: str, content: str
    ) -> list[tuple[str, str, str]]:
        name = Path(path).name
        dependencies: set[tuple[str, str, str]] = set()
        try:
            if name == "pyproject.toml":
                data = tomllib.loads(content)
                for item in data.get("project", {}).get("dependencies", []):
                    package = re.split(r"[\s<>=!~;\[]", str(item), maxsplit=1)[0]
                    if package:
                        dependencies.add(("python", package.lower(), "runtime"))
                for group, items in (
                    data.get("project", {}).get("optional-dependencies", {})
                ).items():
                    for item in items:
                        package = re.split(r"[\s<>=!~;\[]", str(item), maxsplit=1)[0]
                        if package:
                            dependencies.add(("python", package.lower(), str(group)))
            elif name.startswith("requirements"):
                for line in content.splitlines():
                    value = line.strip()
                    if value and not value.startswith(("#", "-", "http")):
                        package = re.split(r"[\s<>=!~;\[]", value, maxsplit=1)[0]
                        if package:
                            dependencies.add(("python", package.lower(), name))
            elif name == "package.json":
                data = json.loads(content)
                for scope in (
                    "dependencies",
                    "devDependencies",
                    "peerDependencies",
                    "optionalDependencies",
                ):
                    for package in data.get(scope, {}):
                        dependencies.add(("javascript", package, scope))
            elif name == "Cargo.toml":
                data = tomllib.loads(content)
                for scope in ("dependencies", "dev-dependencies", "build-dependencies"):
                    for package in data.get(scope, {}):
                        dependencies.add(("rust", package, scope))
            elif name == "composer.json":
                data = json.loads(content)
                for scope in ("require", "require-dev"):
                    for package in data.get(scope, {}):
                        dependencies.add(("php", package, scope))
            elif name == "go.mod":
                for package in re.findall(
                    r"(?m)^\s*([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)\s+v",
                    content,
                ):
                    dependencies.add(("go", package, "require"))
            elif name == "pom.xml":
                for group, artifact in re.findall(
                    r"<dependency>.*?<groupId>(.*?)</groupId>.*?"
                    r"<artifactId>(.*?)</artifactId>.*?</dependency>",
                    content,
                    re.DOTALL,
                ):
                    dependencies.add(
                        ("java", f"{group.strip()}:{artifact.strip()}", "dependency")
                    )
            elif name == "Gemfile":
                for package in re.findall(r"(?m)^\s*gem\s+['\"]([^'\"]+)", content):
                    dependencies.add(("ruby", package, "gem"))
            elif name.startswith("build.gradle"):
                for scope, package in re.findall(
                    r"(?m)^\s*(implementation|api|compileOnly|runtimeOnly|"
                    r"testImplementation)\s*[\(\s]+['\"]([^'\"]+)",
                    content,
                ):
                    dependencies.add(("gradle", package, scope))
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, TypeError):
            return []
        return sorted(dependencies)

    @staticmethod
    def _extract_routes(path: str, content: str) -> list[dict[str, Any]]:
        suffix = Path(path).suffix.lower()
        routes: set[tuple[int, str, str, str]] = set()
        if suffix == ".py":
            try:
                tree = ast.parse(content)
            except SyntaxError:
                tree = None
            if tree:
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    for decorator in node.decorator_list:
                        if not isinstance(decorator, ast.Call):
                            continue
                        function = decorator.func
                        method = (
                            function.attr.upper()
                            if isinstance(function, ast.Attribute)
                            else ""
                        )
                        if method not in {
                            "DELETE",
                            "GET",
                            "HEAD",
                            "OPTIONS",
                            "PATCH",
                            "POST",
                            "PUT",
                            "ROUTE",
                        }:
                            continue
                        if decorator.args and isinstance(decorator.args[0], ast.Constant):
                            route = decorator.args[0].value
                            if isinstance(route, str):
                                routes.add(
                                    (node.lineno, method, route, node.name)
                                )
        elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
            pattern = re.compile(
                r"(?m)(?:app|router|server)\.(get|post|put|patch|delete|options|head)"
                r"\s*\(\s*['\"`]([^'\"`]+)"
            )
            for match in pattern.finditer(content):
                routes.add(
                    (
                        content.count("\n", 0, match.start()) + 1,
                        match.group(1).upper(),
                        match.group(2),
                        "",
                    )
                )
            for match in re.finditer(
                r"(?m)@(Get|Post|Put|Patch|Delete)\s*\(\s*['\"`]([^'\"`]*)",
                content,
            ):
                routes.add(
                    (
                        content.count("\n", 0, match.start()) + 1,
                        match.group(1).upper(),
                        match.group(2) or "/",
                        "",
                    )
                )
        elif suffix == ".rb":
            for match in re.finditer(
                r"(?m)^\s*(get|post|put|patch|delete)\s+['\"]([^'\"]+)",
                content,
            ):
                routes.add(
                    (
                        content.count("\n", 0, match.start()) + 1,
                        match.group(1).upper(),
                        match.group(2),
                        "",
                    )
                )
        return [
            {"line": line, "method": method, "route": route, "handler": handler}
            for line, method, route, handler in sorted(routes)
        ]

    @staticmethod
    def _extract_schemas(path: str, content: str) -> list[dict[str, Any]]:
        suffix = Path(path).suffix.lower()
        schemas: set[tuple[int, str, str]] = set()
        if suffix == ".py":
            try:
                tree = ast.parse(content)
            except SyntaxError:
                tree = None
            if tree:
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        base_names = {
                            base.id
                            for base in node.bases
                            if isinstance(base, ast.Name)
                        } | {
                            base.attr
                            for base in node.bases
                            if isinstance(base, ast.Attribute)
                        }
                        table_name = None
                        for item in node.body:
                            target: ast.expr | None = None
                            value: ast.expr | None = None
                            if isinstance(item, ast.Assign) and item.targets:
                                target = item.targets[0]
                                value = item.value
                            elif isinstance(item, ast.AnnAssign):
                                target = item.target
                                value = item.value
                            if (
                                isinstance(target, ast.Name)
                                and target.id == "__tablename__"
                                and isinstance(value, ast.Constant)
                                and isinstance(value.value, str)
                            ):
                                table_name = value.value
                        if table_name or {"Base", "Model"} & base_names:
                            schemas.add(
                                (
                                    node.lineno,
                                    table_name or node.name,
                                    "orm_model",
                                )
                            )
            for match in re.finditer(
                r"op\.create_table\(\s*['\"]([^'\"]+)", content
            ):
                schemas.add(
                    (
                        content.count("\n", 0, match.start()) + 1,
                        match.group(1),
                        "migration",
                    )
                )
        if suffix == ".sql":
            for match in re.finditer(
                r"(?im)\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?[\"`]?([\w.]+)",
                content,
            ):
                schemas.add(
                    (
                        content.count("\n", 0, match.start()) + 1,
                        match.group(1),
                        "sql_table",
                    )
                )
        if suffix == ".prisma":
            for match in re.finditer(r"(?m)^\s*model\s+(\w+)\s*\{", content):
                schemas.add(
                    (
                        content.count("\n", 0, match.start()) + 1,
                        match.group(1),
                        "prisma_model",
                    )
                )
        if suffix in {".js", ".jsx", ".ts", ".tsx"}:
            for match in re.finditer(
                r"(?m)(?:const|let|var)\s+(\w+Schema)\s*=\s*new\s+(?:mongoose\.)?Schema",
                content,
            ):
                schemas.add(
                    (
                        content.count("\n", 0, match.start()) + 1,
                        match.group(1),
                        "mongoose_schema",
                    )
                )
            for match in re.finditer(r"(?m)@Entity\s*\(\s*['\"`]?(\w*)", content):
                schemas.add(
                    (
                        content.count("\n", 0, match.start()) + 1,
                        match.group(1) or "Entity",
                        "typeorm_entity",
                    )
                )
        return [
            {"line": line, "name": name, "declaration_type": declaration_type}
            for line, name, declaration_type in sorted(schemas)
        ]

    @staticmethod
    def _extract_internal_imports(
        path: str, content: str
    ) -> list[tuple[str, str]]:
        suffix = Path(path).suffix.lower()
        imports: set[tuple[str, str]] = set()
        if suffix == ".py":
            try:
                tree = ast.parse(content)
            except SyntaxError:
                return []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(("python", item.name) for item in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(("python", node.module))
        elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
            for imported in re.findall(
                r"(?:from\s+|require\(\s*)['\"](\.[^'\"]+)['\"]", content
            ):
                imports.add(("relative", imported))
        return sorted(imports)

    @staticmethod
    def _link_imports(
        repository_id: str,
        imports: list[tuple[str, str, str]],
        file_ids: dict[str, str],
        graph: GraphBuilder,
    ) -> None:
        module_targets: dict[str, str] = {}
        id_to_path = {node_id: path for path, node_id in file_ids.items()}
        for path, file_id in file_ids.items():
            path_obj = Path(path)
            if path_obj.suffix == ".py":
                dotted = ".".join(path_obj.with_suffix("").parts)
                module_targets[dotted] = file_id
                if path_obj.name == "__init__.py":
                    module_targets[".".join(path_obj.parent.parts)] = file_id
        for source, kind, imported in sorted(imports):
            target = None
            if kind == "python":
                candidates = [
                    imported,
                    *[
                        ".".join(imported.split(".")[:index])
                        for index in range(len(imported.split(".")) - 1, 0, -1)
                    ],
                ]
                target = next(
                    (module_targets[item] for item in candidates if item in module_targets),
                    None,
                )
            else:
                source_path = id_to_path.get(source)
                if source_path:
                    base = posixpath.normpath(
                        f"{Path(source_path).parent.as_posix()}/{imported}"
                    )
                    candidates = [
                        base,
                        *[
                            f"{base}{suffix}"
                            for suffix in (".ts", ".tsx", ".js", ".jsx")
                        ],
                        *[
                            f"{base}/index{suffix}"
                            for suffix in (".ts", ".tsx", ".js", ".jsx")
                        ],
                    ]
                    target = next(
                        (file_ids[item] for item in candidates if item in file_ids),
                        None,
                    )
            if target and target != source:
                graph.add_edge(source, target, "imports")

    @staticmethod
    def _analyze_impact(
        contract: TaskContractV1, graph: dict[str, Any]
    ) -> dict[str, Any]:
        weighted_terms: dict[str, int] = defaultdict(int)
        sources = [
            (contract.title, 4),
            (contract.description, 2),
            (" ".join(contract.acceptance_criteria), 3),
            (" ".join(contract.labels), 2),
        ]
        for text, weight in sources:
            for term in _tokens(text):
                weighted_terms[term] = max(weighted_terms[term], weight)
        direct: dict[str, dict[str, Any]] = {}
        node_lookup = {node["id"]: node for node in graph["nodes"]}
        for node in graph["nodes"]:
            searchable = " ".join(
                [
                    node.get("label") or "",
                    node.get("path") or "",
                    node.get("type") or "",
                    json.dumps(node.get("metadata", {}), sort_keys=True),
                ]
            ).lower()
            node_terms = set(_tokens(searchable))
            matched = sorted(set(weighted_terms) & node_terms)
            if not matched:
                continue
            score = sum(weighted_terms[term] for term in matched)
            if node["type"] in {
                "api_route",
                "database_schema",
                "dependency",
                "dependency_manifest",
            }:
                score += 2
            direct[node["id"]] = {
                "node_id": node["id"],
                "score": score,
                "matched_terms": matched,
                "reasons": ["task_terms_match_node"],
            }

        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in graph["edges"]:
            adjacency[edge["source"]].add(edge["target"])
            adjacency[edge["target"]].add(edge["source"])
        impacts = dict(direct)
        for node_id, item in sorted(direct.items()):
            for neighbor in sorted(adjacency[node_id]):
                if neighbor in impacts or neighbor not in node_lookup:
                    continue
                propagated = max(1, item["score"] // 2)
                impacts[neighbor] = {
                    "node_id": neighbor,
                    "score": propagated,
                    "matched_terms": item["matched_terms"],
                    "reasons": [f"connected_to:{node_id}"],
                }
        ordered = sorted(
            impacts.values(), key=lambda item: (-item["score"], item["node_id"])
        )
        selected = ordered[:MAX_IMPACT_NODES]
        impacted_repositories = {
            node_lookup[item["node_id"]]["repository"]
            for item in selected
            if item["node_id"] in node_lookup
        }
        return {
            "query_terms": sorted(weighted_terms),
            "impacted_nodes": selected,
            "impacted_repository_count": len(impacted_repositories),
            "truncated": len(ordered) > MAX_IMPACT_NODES,
        }

    @staticmethod
    def _analyze_risk(
        contract: TaskContractV1,
        repositories: list[ApplicationContextRepository],
        graph: dict[str, Any],
        impact: dict[str, Any],
    ) -> dict[str, Any]:
        factors: list[dict[str, Any]] = []

        def add(code: str, contribution: int, explanation: str) -> None:
            if contribution:
                factors.append(
                    {
                        "code": code,
                        "contribution": contribution,
                        "explanation": explanation,
                    }
                )

        unavailable = sum(row.status == "unavailable" for row in repositories)
        add(
            "no_repositories",
            25 if not repositories else 0,
            "The task contract does not identify a repository.",
        )
        add(
            "unavailable_repositories",
            min(30, unavailable * 15),
            f"{unavailable} requested repositories are unavailable.",
        )
        impacted_ids = {
            item["node_id"] for item in impact.get("impacted_nodes", [])
        }
        impacted_types = {
            node["type"] for node in graph["nodes"] if node["id"] in impacted_ids
        }
        add(
            "database_change",
            20 if "database_schema" in impacted_types else 0,
            "Impact analysis reaches database or schema declarations.",
        )
        add(
            "api_change",
            15 if "api_route" in impacted_types else 0,
            "Impact analysis reaches HTTP API routes.",
        )
        add(
            "dependency_change",
            15
            if {"dependency", "dependency_manifest"} & impacted_types
            else 0,
            "Impact analysis reaches dependency declarations.",
        )
        impacted_count = len(impact.get("impacted_nodes", []))
        breadth = min(20, (impacted_count // 10) * 5)
        add(
            "impact_breadth",
            breadth,
            f"{impacted_count} graph nodes are in the bounded impact set.",
        )
        repo_count = impact.get("impacted_repository_count", 0)
        add(
            "cross_repository_change",
            10 if repo_count > 1 else 0,
            f"Impact spans {repo_count} repositories.",
        )
        task_terms = set(
            _tokens(
                " ".join(
                    [
                        contract.title,
                        contract.description,
                        *contract.acceptance_criteria,
                        *contract.labels,
                    ]
                )
            )
        )
        risky_terms = sorted(task_terms & HIGH_RISK_TERMS)
        add(
            "sensitive_change_terms",
            min(20, len(risky_terms) * 5),
            "Sensitive task terms: " + ", ".join(risky_terms),
        )
        add(
            "missing_acceptance_criteria",
            10 if not contract.acceptance_criteria else 0,
            "The task contract has no acceptance criteria.",
        )
        add(
            "truncated_graph",
            10 if graph.get("truncated") else 0,
            "The graph reached a deterministic scan bound.",
        )
        score = min(100, sum(item["contribution"] for item in factors))
        level = (
            "critical"
            if score >= 80
            else "high"
            if score >= 60
            else "medium"
            if score >= 30
            else "low"
        )
        return {"score": score, "level": level, "factors": factors}

    @staticmethod
    def _read(context: ApplicationContext) -> ApplicationContextRead:
        return ApplicationContextRead.model_validate(
            {
                "id": context.id,
                "task_id": context.task_id,
                "schema_version": context.schema_version,
                "status": context.status,
                "scanner_version": context.scanner_version,
                "graph": context.graph,
                "graph_hash": context.graph_hash,
                "impact_analysis": context.impact_analysis,
                "risk_analysis": context.risk_analysis,
                "repositories": [
                    RepositoryContextRead.model_validate(repository)
                    for repository in context.repositories
                ],
                "created_at": context.created_at,
                "updated_at": context.updated_at,
            }
        )


def _tokens(value: str) -> list[str]:
    return sorted(
        {
            token
            for token in re.findall(r"[a-z0-9]+", value.lower())
            if len(token) >= 2 and token not in STOP_WORDS
        }
    )
