import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

MAX_SCIP_JSON_BYTES = 8 * 1024 * 1024
MAX_SCIP_JSON_CONTAINERS = 100_000
MAX_SCIP_JSON_SEPARATORS = 500_000
MAX_SCIP_JSON_DEPTH = 64
MAX_SCIP_DOCUMENTS = 20_000
MAX_SCIP_OCCURRENCES = 200_000
MAX_SCIP_SYMBOLS = 50_000
MAX_SCIP_RELATIONSHIPS = 100_000
MAX_SCIP_SYMBOL_LENGTH = 4_096
DEFAULT_SCIP_JSON_PATH = ".sacm/index.scip.json"


@dataclass
class CodeIntelligenceImport:
    status: str = "unavailable"
    source: str = "none"
    fingerprint: str | None = None
    tool_name: str | None = None
    tool_version: str | None = None
    document_count: int = 0
    symbol_count: int = 0
    occurrence_count: int = 0
    errors: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "document_count": self.document_count,
            "errors": self.errors,
            "fingerprint": self.fingerprint,
            "occurrence_count": self.occurrence_count,
            "source": self.source,
            "status": self.status,
            "symbol_count": self.symbol_count,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
        }


class CodeIntelligenceAdapter(Protocol):
    def merge(
        self,
        repository_root: Path,
        repository_id: str,
        graph: "GraphSink",
        file_ids: dict[str, str],
    ) -> CodeIntelligenceImport: ...


class GraphSink(Protocol):
    nodes: dict[str, dict[str, Any]]

    def add_node(self, node: dict[str, Any]) -> bool: ...

    def add_edge(self, source: str, target: str, edge_type: str) -> None: ...


class ScipJsonAdapter:
    """Imports semantic facts emitted by the canonical `scip print --json` CLI."""

    def __init__(self, relative_path: str | None = None):
        configured = (
            relative_path
            or os.getenv("SACM_SCIP_JSON_PATH")
            or DEFAULT_SCIP_JSON_PATH
        )
        path = PurePosixPath(configured)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("SACM_SCIP_JSON_PATH must be repository-relative.")
        self.relative_path = path.as_posix()

    def merge(
        self,
        repository_root: Path,
        repository_id: str,
        graph: GraphSink,
        file_ids: dict[str, str],
    ) -> CodeIntelligenceImport:
        result = CodeIntelligenceImport(source="scip-json/v1")
        index_path = (repository_root / self.relative_path).resolve()
        if repository_root not in index_path.parents or not index_path.is_file():
            return result
        try:
            size = index_path.stat().st_size
            if size > MAX_SCIP_JSON_BYTES:
                result.errors.append("scip_index_oversized")
                return result
            raw = index_path.read_bytes()
            if not self._preflight_json(raw):
                result.errors.append("scip_index_structure_limit_exceeded")
                return result
            payload = json.loads(raw)
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            result.errors.append("scip_index_invalid")
            return result
        if not isinstance(payload, dict):
            result.errors.append("scip_index_invalid")
            return result

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            result.errors.append("scip_index_invalid")
            return result
        tool = self._value(metadata, "tool_info", "toolInfo") or {}
        if not isinstance(tool, dict):
            tool = {}
        result.tool_name = str(tool.get("name") or "") or None
        result.tool_version = str(tool.get("version") or "") or None
        result.fingerprint = hashlib.sha256(raw).hexdigest()
        documents = payload.get("documents") or []
        if not isinstance(documents, list):
            result.errors.append("scip_documents_invalid")
            return result

        symbols: dict[str, str] = {}
        symbol_information: dict[str, tuple[dict[str, Any], str | None]] = {}
        external_symbols = payload.get(
            "external_symbols", payload.get("externalSymbols", [])
        )
        if not isinstance(external_symbols, list):
            external_symbols = []
        for item in external_symbols[:MAX_SCIP_SYMBOLS]:
            if isinstance(item, dict) and self._valid_symbol(item.get("symbol")):
                symbol = str(item["symbol"])
                symbol_information[symbol] = (item, None)
        if len(external_symbols) > MAX_SCIP_SYMBOLS:
            result.errors.append("scip_symbol_limit_exceeded")
        if len(documents) > MAX_SCIP_DOCUMENTS:
            result.errors.append("scip_document_limit_exceeded")
            documents = documents[:MAX_SCIP_DOCUMENTS]
        occurrence_limit_reached = False
        symbol_limit_reached = len(symbol_information) >= MAX_SCIP_SYMBOLS
        for document in documents:
            if symbol_limit_reached:
                break
            if not isinstance(document, dict):
                continue
            relative_path = self._value(document, "relative_path", "relativePath")
            if not isinstance(relative_path, str) or not self._canonical(relative_path):
                continue
            document_symbols = document.get("symbols") or []
            if not isinstance(document_symbols, list):
                continue
            for item in document_symbols:
                if len(symbol_information) >= MAX_SCIP_SYMBOLS:
                    result.errors.append("scip_symbol_limit_exceeded")
                    symbol_limit_reached = True
                    break
                if isinstance(item, dict) and self._valid_symbol(item.get("symbol")):
                    symbol = str(item["symbol"])
                    key = self._symbol_key(relative_path, symbol)
                    symbol_information[key] = (item, relative_path)

        for document in documents:
            if occurrence_limit_reached:
                break
            if not isinstance(document, dict):
                continue
            relative_path = self._value(document, "relative_path", "relativePath")
            if not isinstance(relative_path, str) or not self._canonical(relative_path):
                result.errors.append("scip_document_path_invalid")
                continue
            file_id = file_ids.get(relative_path)
            if file_id is None:
                continue
            result.document_count += 1
            occurrences = document.get("occurrences") or []
            if not isinstance(occurrences, list):
                result.errors.append("scip_occurrences_invalid")
                continue
            for occurrence in occurrences:
                if not isinstance(occurrence, dict):
                    continue
                occurrence_symbol = occurrence.get("symbol")
                if not self._valid_symbol(occurrence_symbol):
                    continue
                assert isinstance(occurrence_symbol, str)
                if result.occurrence_count >= MAX_SCIP_OCCURRENCES:
                    result.errors.append("scip_occurrence_limit_exceeded")
                    occurrence_limit_reached = True
                    break
                symbol_key = self._symbol_key(relative_path, occurrence_symbol)
                symbol_id = symbols.get(symbol_key)
                if symbol_id is None:
                    if len(symbols) >= MAX_SCIP_SYMBOLS:
                        self._record_error(
                            result.errors, "scip_symbol_limit_exceeded"
                        )
                        symbol_limit_reached = True
                        break
                    information = symbol_information.get(symbol_key, ({}, None))[0]
                    symbol_id = self._symbol_node(
                        repository_id,
                        symbol_key,
                        occurrence_symbol,
                        information,
                        result,
                        graph,
                    )
                    symbols[symbol_key] = symbol_id
                roles = self._integer(
                    self._value(occurrence, "symbol_roles", "symbolRoles")
                )
                line = self._line(occurrence)
                node = graph.nodes.get(symbol_id)
                if node is not None and roles & 1:
                    definitions = node["metadata"].setdefault("definitions", [])
                    definition = {"path": relative_path, "line": line}
                    if definition not in definitions and len(definitions) < 20:
                        definitions.append(definition)
                graph.add_edge(
                    file_id,
                    symbol_id,
                    (
                        "defines_semantic_symbol"
                        if roles & 1
                        else "references_semantic_symbol"
                    ),
                )
                if roles & 32:
                    graph.add_edge(file_id, symbol_id, "tests_semantic_symbol")
                result.occurrence_count += 1

        relationship_count = 0
        for symbol_key, (information, document_path) in symbol_information.items():
            source_id = symbols.get(symbol_key)
            if source_id is None:
                continue
            relationships = information.get("relationships") or []
            if not isinstance(relationships, list):
                continue
            for relationship in relationships:
                if relationship_count >= MAX_SCIP_RELATIONSHIPS:
                    result.errors.append("scip_relationship_limit_exceeded")
                    break
                if not isinstance(relationship, dict):
                    continue
                target_symbol = relationship.get("symbol")
                if not self._valid_symbol(target_symbol):
                    continue
                assert isinstance(target_symbol, str)
                target_key = self._symbol_key(document_path or "", target_symbol)
                target_id = symbols.get(target_key)
                if target_id is None:
                    if len(symbols) >= MAX_SCIP_SYMBOLS:
                        self._record_error(
                            result.errors, "scip_symbol_limit_exceeded"
                        )
                        break
                    target_information = symbol_information.get(
                        target_key, ({}, None)
                    )[0]
                    target_id = self._symbol_node(
                        repository_id,
                        target_key,
                        target_symbol,
                        target_information,
                        result,
                        graph,
                    )
                    symbols[target_key] = target_id
                for edge_type in self._relationship_types(relationship):
                    graph.add_edge(source_id, target_id, edge_type)
                    relationship_count += 1

        self._link_syntactic_fallback(graph, symbols)
        result.symbol_count = len(symbols)
        result.status = "available" if result.document_count else "unavailable"
        return result

    @staticmethod
    def _symbol_node(
        repository_id: str,
        symbol_key: str,
        symbol: str,
        information: dict[str, Any],
        result: CodeIntelligenceImport,
        graph: GraphSink,
    ) -> str:
        digest = hashlib.sha256(symbol_key.encode()).hexdigest()[:24]
        symbol_id = f"{repository_id}:semantic_symbol:{digest}"
        display_name = str(
            information.get("display_name")
            or information.get("displayName")
            or ScipJsonAdapter._simple_name(symbol)
        )[:500]
        graph.add_node(
            {
                "id": symbol_id,
                "type": "semantic_symbol",
                "repository": repository_id,
                "label": display_name,
                "path": None,
                "metadata": {
                    "kind": information.get("kind"),
                    "semantic": True,
                    "symbol": symbol,
                    "tool_name": result.tool_name,
                    "tool_version": result.tool_version,
                },
            }
        )
        return symbol_id

    @staticmethod
    def _link_syntactic_fallback(
        graph: GraphSink, symbols: dict[str, str]
    ) -> None:
        semantic_nodes = {
            node_id: graph.nodes[node_id]
            for node_id in symbols.values()
            if node_id in graph.nodes
        }
        for node_id, node in list(graph.nodes.items()):
            if node.get("type") not in {"symbol", "test_symbol"}:
                continue
            matches = [
                semantic_id
                for semantic_id, semantic in semantic_nodes.items()
                if semantic.get("label") == node.get("label")
                and any(
                    definition.get("path") == node.get("path")
                    for definition in semantic.get("metadata", {}).get(
                        "definitions", []
                    )
                )
            ]
            if len(matches) == 1:
                graph.add_edge(node_id, matches[0], "resolved_as")

    @staticmethod
    def _relationship_types(relationship: dict[str, Any]) -> list[str]:
        result: list[str] = []
        mapping = (
            ("is_implementation", "isImplementation", "implements"),
            ("is_type_definition", "isTypeDefinition", "type_definition"),
            ("is_definition", "isDefinition", "definition_of"),
            ("is_reference", "isReference", "semantic_reference"),
        )
        for snake, camel, edge_type in mapping:
            if relationship.get(snake) or relationship.get(camel):
                result.append(edge_type)
        return result

    @staticmethod
    def _line(occurrence: dict[str, Any]) -> int | None:
        single = occurrence.get("single_line_range") or occurrence.get(
            "singleLineRange"
        )
        if isinstance(single, dict) and single.get("line") is not None:
            return ScipJsonAdapter._integer(single["line"]) + 1
        if isinstance(single, dict) and single.get("start_line") is not None:
            return ScipJsonAdapter._integer(single["start_line"]) + 1
        if isinstance(single, dict) and single.get("startLine") is not None:
            return ScipJsonAdapter._integer(single["startLine"]) + 1
        multi = occurrence.get("multi_line_range") or occurrence.get(
            "multiLineRange"
        )
        if isinstance(multi, dict) and multi.get("start_line") is not None:
            return ScipJsonAdapter._integer(multi["start_line"]) + 1
        if isinstance(multi, dict) and multi.get("startLine") is not None:
            return ScipJsonAdapter._integer(multi["startLine"]) + 1
        value = occurrence.get("range")
        if isinstance(value, list) and value:
            return ScipJsonAdapter._integer(value[0]) + 1
        return None

    @staticmethod
    def _canonical(value: str) -> bool:
        path = PurePosixPath(value)
        parts = value.split("/")
        return bool(
            value
            and not path.is_absolute()
            and ".." not in parts
            and "." not in parts
            and "\\" not in value
            and "//" not in value
        )

    @staticmethod
    def _value(payload: dict[str, Any], snake: str, camel: str) -> Any:
        return payload.get(snake, payload.get(camel))

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _valid_symbol(value: Any) -> bool:
        return isinstance(value, str) and 0 < len(value) <= MAX_SCIP_SYMBOL_LENGTH

    @staticmethod
    def _symbol_key(relative_path: str, symbol: str) -> str:
        return f"{relative_path}\0{symbol}" if symbol.startswith("local ") else symbol

    @staticmethod
    def _simple_name(symbol: str) -> str:
        function = re.search(r"([A-Za-z_$][\w$]*)\(\)\.?$", symbol)
        if function:
            return function.group(1)
        descriptor = re.search(r"([A-Za-z_$][\w$]*)[#./]?$", symbol)
        return descriptor.group(1) if descriptor else symbol[-500:]

    @staticmethod
    def _preflight_json(raw: bytes) -> bool:
        in_string = False
        escaped = False
        depth = 0
        containers = 0
        separators = 0
        for value in raw:
            character = chr(value)
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character in "[{":
                depth += 1
                containers += 1
                if (
                    depth > MAX_SCIP_JSON_DEPTH
                    or containers > MAX_SCIP_JSON_CONTAINERS
                ):
                    return False
            elif character in "]}":
                depth -= 1
                if depth < 0:
                    return False
            elif character == ",":
                separators += 1
                if separators > MAX_SCIP_JSON_SEPARATORS:
                    return False
        return depth == 0 and not in_string

    @staticmethod
    def _record_error(errors: list[str], code: str) -> None:
        if code not in errors:
            errors.append(code)
