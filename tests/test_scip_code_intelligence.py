import json

import sacm.adapters.code_intelligence as code_intelligence
from sacm.core.application_context_service import ApplicationContextService
from sacm.core.context_engine_service import ContextEngineService
from sacm.core.task_intake_service import TaskIntakeService
from sacm.schemas.application_context import ContextExpansionRequest
from sacm.schemas.task import RepositoryReference, TaskContractV1

CREATE_ORDER = "scip-python python orders 1.0 app/create_order()."
VALIDATE_ORDER = "scip-python python orders 1.0 app/validate_order()."


def _task(db, tmp_path, monkeypatch):
    repository = tmp_path / "orders"
    repository.mkdir()
    (repository / "app.py").write_text(
        """
def validate_order(order):
    return bool(order)

def create_order(order):
    return validate_order(order)
""".strip(),
        encoding="utf-8",
    )
    tests = repository / "tests"
    tests.mkdir()
    (tests / "test_orders.py").write_text(
        """
from app import create_order

def test_create_order():
    assert create_order({"sku": "1"})
""".strip(),
        encoding="utf-8",
    )
    index_dir = repository / ".sacm"
    index_dir.mkdir()
    index_path = index_dir / "index.scip.json"
    index_path.write_text(
        json.dumps(_index()),
        encoding="utf-8",
    )
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    task, _, _ = TaskIntakeService(db).ingest(
        TaskContractV1(
            connector_type="generic",
            external_id="scip-context",
            title="Repair order validation",
            description="Fix create_order without breaking its callers.",
            acceptance_criteria=["The focused order test passes."],
            repositories=[RepositoryReference(path=str(repository))],
            requested_by="platform",
        )
    )
    return task, index_path


def _index():
    return {
        "metadata": {
            "tool_info": {
                "name": "scip-python",
                "version": "0.6.11",
                "arguments": [],
            },
            "project_root": "file:///workspace/orders",
        },
        "documents": [
            {
                "relative_path": "app.py",
                "symbols": [
                    {
                        "symbol": VALIDATE_ORDER,
                        "display_name": "validate_order",
                        "kind": 17,
                        "relationships": [],
                    },
                    {
                        "symbol": CREATE_ORDER,
                        "display_name": "create_order",
                        "kind": 17,
                        "relationships": [
                            {
                                "symbol": VALIDATE_ORDER,
                                "is_reference": True,
                            }
                        ],
                    },
                ],
                "occurrences": [
                    {
                        "range": [0, 4, 0, 18],
                        "symbol": VALIDATE_ORDER,
                        "symbol_roles": 1,
                    },
                    {
                        "range": [3, 4, 3, 16],
                        "symbol": CREATE_ORDER,
                        "symbol_roles": 1,
                    },
                    {
                        "range": [4, 11, 4, 25],
                        "symbol": VALIDATE_ORDER,
                        "symbol_roles": 8,
                    },
                ],
            },
            {
                "relative_path": "tests/test_orders.py",
                "symbols": [],
                "occurrences": [
                    {
                        "range": [3, 4, 3, 21],
                        "symbol": CREATE_ORDER,
                        "symbol_roles": 40,
                    }
                ],
            },
        ],
        "external_symbols": [],
    }


def test_scip_index_adds_type_aware_graph_and_fingerprint(
    db, tmp_path, monkeypatch
):
    task, _ = _task(db, tmp_path, monkeypatch)

    context = ApplicationContextService(db).build(task.id)

    semantic = {
        node.label: node
        for node in context.graph.nodes
        if node.type == "semantic_symbol"
    }
    assert set(semantic) == {"create_order", "validate_order"}
    assert semantic["create_order"].metadata["semantic"] is True
    edge_types = {edge.type for edge in context.graph.edges}
    assert {
        "defines_semantic_symbol",
        "references_semantic_symbol",
        "tests_semantic_symbol",
        "semantic_reference",
        "resolved_as",
    } <= edge_types
    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "available"
    assert metadata["tool_name"] == "scip-python"
    assert metadata["tool_version"] == "0.6.11"
    assert len(metadata["fingerprint"]) == 64


def test_scip_fingerprint_changes_application_graph_hash(
    db, tmp_path, monkeypatch
):
    task, index_path = _task(db, tmp_path, monkeypatch)
    service = ApplicationContextService(db)
    first = service.build(task.id)
    payload = _index()
    payload["metadata"]["tool_info"]["version"] = "0.6.12"
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    second = service.build(task.id)

    assert first.graph_hash != second.graph_hash
    assert (
        first.repositories[0].scan_metadata["code_intelligence"]["fingerprint"]
        != second.repositories[0].scan_metadata["code_intelligence"]["fingerprint"]
    )


def test_context_traversal_prefers_semantic_scip_symbols(
    db, tmp_path, monkeypatch
):
    task, _ = _task(db, tmp_path, monkeypatch)

    package = ContextEngineService(db).build(
        task.id,
        ContextExpansionRequest(failing_symbols=["create_order"]),
    )

    semantic = [
        node
        for node in package.nodes
        if node.type == "semantic_symbol" and node.label == "create_order"
    ]
    assert semantic
    assert semantic[0].distance == 0
    assert any(edge.type == "resolved_as" for edge in package.edges)
    assert any(
        edge.type in {"tests_semantic_symbol", "semantic_reference"}
        for edge in package.edges
    )


def test_invalid_scip_document_path_is_rejected(db, tmp_path, monkeypatch):
    task, index_path = _task(db, tmp_path, monkeypatch)
    payload = _index()
    payload["documents"][0]["relative_path"] = "../outside.py"
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "available"
    assert "scip_document_path_invalid" in metadata["errors"]
    assert not any(
        node.type == "semantic_symbol" and node.path == "../outside.py"
        for node in context.graph.nodes
    )


def test_local_symbols_are_isolated_per_document(db, tmp_path, monkeypatch):
    task, index_path = _task(db, tmp_path, monkeypatch)
    payload = _index()
    for document in payload["documents"]:
        document["symbols"].append(
            {
                "symbol": "local 0",
                "displayName": "local_helper",
                "relationships": [],
            }
        )
        document["occurrences"].append(
            {
                "singleLineRange": {
                    "line": 1,
                    "startCharacter": 0,
                    "endCharacter": 5,
                },
                "symbol": "local 0",
                "symbolRoles": 1,
            }
        )
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    context = ApplicationContextService(db).build(task.id)

    local_nodes = [
        node
        for node in context.graph.nodes
        if node.type == "semantic_symbol"
        and node.metadata["symbol"] == "local 0"
    ]
    assert len(local_nodes) == 2
    definition_paths = {
        node.metadata["definitions"][0]["path"] for node in local_nodes
    }
    assert definition_paths == {"app.py", "tests/test_orders.py"}
    assert {node.metadata["definitions"][0]["line"] for node in local_nodes} == {2}


def test_malformed_scip_metadata_fails_closed(db, tmp_path, monkeypatch):
    task, index_path = _task(db, tmp_path, monkeypatch)
    index_path.write_text(
        json.dumps({"metadata": [], "documents": []}),
        encoding="utf-8",
    )

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "unavailable"
    assert metadata["errors"] == ["scip_index_invalid"]


def test_scip_relationship_emits_all_asserted_semantics(
    db, tmp_path, monkeypatch
):
    task, index_path = _task(db, tmp_path, monkeypatch)
    payload = _index()
    payload["documents"][0]["symbols"][1]["relationships"][0].update(
        {"isImplementation": True, "isTypeDefinition": True}
    )
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    context = ApplicationContextService(db).build(task.id)

    edge_types = {edge.type for edge in context.graph.edges}
    assert {
        "implements",
        "type_definition",
        "semantic_reference",
    } <= edge_types


def test_missing_display_name_uses_scip_descriptor_name(
    db, tmp_path, monkeypatch
):
    task, index_path = _task(db, tmp_path, monkeypatch)
    payload = _index()
    del payload["documents"][0]["symbols"][1]["display_name"]
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    context = ApplicationContextService(db).build(task.id)

    semantic = next(
        node
        for node in context.graph.nodes
        if node.type == "semantic_symbol"
        and node.metadata["symbol"] == CREATE_ORDER
    )
    assert semantic.label == "create_order"
    assert any(
        edge.type == "resolved_as" and edge.target == semantic.id
        for edge in context.graph.edges
    )


def test_scip_json_structure_limit_is_checked_before_deserialization(
    db, tmp_path, monkeypatch
):
    task, index_path = _task(db, tmp_path, monkeypatch)
    index_path.write_text("[" * 65 + "0" + "]" * 65, encoding="utf-8")

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "unavailable"
    assert metadata["errors"] == ["scip_index_structure_limit_exceeded"]


def test_occurrence_only_symbols_respect_semantic_symbol_limit(
    db, tmp_path, monkeypatch
):
    task, index_path = _task(db, tmp_path, monkeypatch)
    payload = _index()
    payload["documents"][0]["occurrences"].append(
        {
            "range": [1, 0, 1, 5],
            "symbol": "scip-python python orders 1.0 app/extra().",
            "symbol_roles": 8,
        }
    )
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(code_intelligence, "MAX_SCIP_SYMBOLS", 2)

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert "scip_symbol_limit_exceeded" in metadata["errors"]
    assert (
        len(
            [
                node
                for node in context.graph.nodes
                if node.type == "semantic_symbol"
            ]
        )
        == 2
    )


def test_oversized_json_integer_fails_closed(db, tmp_path, monkeypatch):
    task, index_path = _task(db, tmp_path, monkeypatch)
    index_path.write_text(
        '{"metadata":{"tool_info":{"name":'
        + "9" * 5_000
        + '}},"documents":[]}',
        encoding="utf-8",
    )

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "unavailable"
    assert metadata["errors"] == ["scip_index_invalid"]
