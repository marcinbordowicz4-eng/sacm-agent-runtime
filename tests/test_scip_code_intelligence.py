import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

import sacm.adapters.code_intelligence as code_intelligence
from sacm.core.application_context_service import ApplicationContextService
from sacm.core.code_intelligence_service import (
    ScipIndexingService,
    inspect_repository_state,
)
from sacm.core.context_engine_service import ContextEngineService
from sacm.core.task_intake_service import TaskIntakeService
from sacm.schemas.application_context import ContextExpansionRequest
from sacm.schemas.task import RepositoryReference, TaskContractV1

CREATE_ORDER = "scip-python python orders 1.0 app/create_order()."
VALIDATE_ORDER = "scip-python python orders 1.0 app/validate_order()."


def _write_index(index_path, content: str) -> None:
    index_path.write_text(content, encoding="utf-8")
    manifest_path = index_path.with_name("index.scip.meta.json")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["index_sha256"] = hashlib.sha256(content.encode()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


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
    index_content = json.dumps(_index())
    _write_index(index_path, index_content)
    state = inspect_repository_state(repository)
    (index_dir / "index.scip.meta.json").write_text(
        json.dumps(
            {
                "schema_version": "code-intelligence-snapshot/v1",
                "repository_revision": state.revision,
                "workspace_hash": state.workspace_hash,
                "workspace_complete": state.fingerprint_complete,
                "index_sha256": hashlib.sha256(index_content.encode()).hexdigest(),
                "generated_at": "2026-08-01T12:00:00+00:00",
            }
        ),
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
    assert metadata["status"] == "COMPLETE"
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
    _write_index(index_path, json.dumps(payload))

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
    _write_index(index_path, json.dumps(payload))

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "PARTIAL"
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
    _write_index(index_path, json.dumps(payload))

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
    _write_index(index_path, json.dumps({"metadata": [], "documents": []}))

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "INVALID"
    assert metadata["errors"] == ["scip_index_invalid"]


def test_scip_relationship_emits_all_asserted_semantics(
    db, tmp_path, monkeypatch
):
    task, index_path = _task(db, tmp_path, monkeypatch)
    payload = _index()
    payload["documents"][0]["symbols"][1]["relationships"][0].update(
        {"isImplementation": True, "isTypeDefinition": True}
    )
    _write_index(index_path, json.dumps(payload))

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
    _write_index(index_path, json.dumps(payload))

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
    _write_index(index_path, "[" * 65 + "0" + "]" * 65)

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "TRUNCATED"
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
    _write_index(index_path, json.dumps(payload))
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
    _write_index(
        index_path,
        '{"metadata":{"tool_info":{"name":'
        + "9" * 5_000
        + '}},"documents":[]}',
    )

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "INVALID"
    assert metadata["errors"] == ["scip_index_invalid"]


def test_changed_workspace_marks_scip_snapshot_stale_and_raises_risk(
    db, tmp_path, monkeypatch
):
    task, _ = _task(db, tmp_path, monkeypatch)
    app_path = tmp_path / "orders" / "app.py"
    app_path.write_text(
        app_path.read_text(encoding="utf-8") + "\n# changed after indexing\n",
        encoding="utf-8",
    )

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "STALE"
    assert metadata["repository_revision"] != metadata["index_revision"]
    assert not any(node.type == "semantic_symbol" for node in context.graph.nodes)
    assert any(
        factor.code == "code_intelligence_incomplete"
        for factor in context.risk_analysis.factors
    )


def test_canonical_semantic_symbol_is_shared_across_repositories(
    db, tmp_path, monkeypatch
):
    repositories = []
    for name in ("backend", "sdk"):
        repository = tmp_path / name
        repository.mkdir()
        (repository / "app.py").write_text(
            "def create_order(order):\n    return order\n",
            encoding="utf-8",
        )
        index_dir = repository / ".sacm"
        index_dir.mkdir()
        payload = _index()
        payload["documents"] = [payload["documents"][0]]
        payload["documents"][0]["relative_path"] = "app.py"
        payload["documents"][0]["symbols"] = [
            payload["documents"][0]["symbols"][1]
        ]
        payload["documents"][0]["occurrences"] = [
            payload["documents"][0]["occurrences"][1]
        ]
        index_content = json.dumps(payload)
        (index_dir / "index.scip.json").write_text(
            index_content, encoding="utf-8"
        )
        state = inspect_repository_state(repository)
        (index_dir / "index.scip.meta.json").write_text(
            json.dumps(
                {
                    "schema_version": "code-intelligence-snapshot/v1",
                    "repository_revision": state.revision,
                    "workspace_hash": state.workspace_hash,
                    "workspace_complete": state.fingerprint_complete,
                    "index_sha256": hashlib.sha256(
                        index_content.encode()
                    ).hexdigest(),
                    "generated_at": "2026-08-01T12:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        repositories.append(RepositoryReference(path=str(repository)))
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    task, _, _ = TaskIntakeService(db).ingest(
        TaskContractV1(
            connector_type="generic",
            external_id="cross-repo-scip",
            title="Update create_order across backend and SDK",
            description="Keep the SDK aligned with the backend order API.",
            acceptance_criteria=["Both repositories expose create_order."],
            repositories=repositories,
            requested_by="platform",
        )
    )

    context = ApplicationContextService(db).build(task.id)

    canonical = [
        node
        for node in context.graph.nodes
        if node.type == "semantic_symbol"
        and node.metadata["symbol"] == CREATE_ORDER
    ]
    assert len(canonical) == 1
    assert canonical[0].repository == "canonical"
    assert {
        item["repository"] for item in canonical[0].metadata["definitions"]
    } == {"repository:000", "repository:001"}
    assert context.impact_analysis.impacted_repository_count == 2


def test_automatic_indexing_runs_configured_indexer_and_binds_snapshot(
    db, tmp_path, monkeypatch
):
    repository = tmp_path / "automatic"
    repository.mkdir()
    (repository / "app.py").write_text(
        "def create_order(order):\n    return order\n",
        encoding="utf-8",
    )
    indexer = repository / "fake_indexer.py"
    indexer.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_bytes(b'index')\n"
        "counter = Path(sys.argv[2]) / '.sacm' / 'indexer-count'\n"
        "counter.parent.mkdir(parents=True, exist_ok=True)\n"
        "count = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(count + 1))\n",
        encoding="utf-8",
    )
    printer = repository / "fake_printer.py"
    payload = _index()
    payload["documents"] = [payload["documents"][0]]
    payload["documents"][0]["symbols"] = [
        payload["documents"][0]["symbols"][1]
    ]
    payload["documents"][0]["occurrences"] = [
        payload["documents"][0]["occurrences"][1]
    ]
    printer.write_text(
        "import json\n"
        f"print(json.dumps({payload!r}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    monkeypatch.setenv("SACM_SCIP_AUTO_INDEX", "true")
    monkeypatch.setenv(
        "SACM_SCIP_INDEXERS_JSON",
        json.dumps(
            {
                "python": {
                    "name": "fake-scip-python",
                    "version": "1",
                    "extensions": [".py"],
                    "index_command": [
                        sys.executable,
                        str(indexer),
                        "{index}",
                        "{repository}",
                    ],
                    "print_command": [
                        sys.executable,
                        str(printer),
                        "{index}",
                    ],
                }
            }
        ),
    )
    task, _, _ = TaskIntakeService(db).ingest(
        TaskContractV1(
            connector_type="generic",
            external_id="automatic-scip",
            title="Index create_order automatically",
            description="Generate revision-bound code intelligence.",
            acceptance_criteria=["The semantic symbol is available."],
            repositories=[RepositoryReference(path=str(repository))],
            requested_by="platform",
        )
    )

    context = ApplicationContextService(db).build(task.id)
    second = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "COMPLETE"
    assert metadata["repository_revision"] == metadata["index_revision"]
    assert metadata["workspace_hash"] == metadata["index_workspace_hash"]
    assert metadata["indexers"][0]["name"] == "fake-scip-python"
    assert (repository / ".sacm" / "index.scip.json").is_file()
    assert any(
        node.type == "semantic_symbol" and node.label == "create_order"
        for node in context.graph.nodes
    )
    assert second.graph_hash == context.graph_hash
    assert (repository / ".sacm" / "indexer-count").read_text() == "1"


def test_snapshot_digest_mismatch_is_invalid(db, tmp_path, monkeypatch):
    task, index_path = _task(db, tmp_path, monkeypatch)
    index_path.write_text(json.dumps(_index()) + " ", encoding="utf-8")

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["status"] == "INVALID"
    assert metadata["errors"] == ["scip_snapshot_digest_mismatch"]


def test_local_symbols_include_merged_indexer_provenance(
    db, tmp_path, monkeypatch
):
    task, index_path = _task(db, tmp_path, monkeypatch)
    document = {
        "relative_path": "app.py",
        "symbols": [
            {
                "symbol": "local 0",
                "display_name": "helper",
                "relationships": [],
            }
        ],
        "occurrences": [
            {
                "singleLineRange": {
                    "line": 0,
                    "startCharacter": 0,
                    "endCharacter": 5,
                },
                "symbol": "local 0",
                "symbolRoles": 1,
            }
        ],
    }
    first = {**document, "sacm_indexer": "python"}
    second = {
        **document,
        "sacm_indexer": "typescript",
        "symbols": [dict(document["symbols"][0])],
        "occurrences": [dict(document["occurrences"][0])],
    }
    payload = _index()
    payload["documents"] = [first, second]
    _write_index(index_path, json.dumps(payload))

    context = ApplicationContextService(db).build(task.id)

    local_nodes = [
        node
        for node in context.graph.nodes
        if node.type == "semantic_symbol"
        and node.metadata["symbol"] == "local 0"
    ]
    assert len(local_nodes) == 2
    assert {
        node.metadata["definitions"][0]["indexer"] for node in local_nodes
    } == {"python", "typescript"}


def test_indexer_output_is_bounded_while_process_is_running(tmp_path):
    with pytest.raises(ValueError, match="output exceeded"):
        ScipIndexingService._run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 10000)",
            ],
            cwd=tmp_path,
            capture_limit=100,
        )


def test_repository_state_detects_untracked_source_files(tmp_path):
    repository = tmp_path / "git-repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "tracked.py"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=SACM Test",
            "-c",
            "user.email=sacm@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )
    clean = inspect_repository_state(repository)
    (repository / "untracked.py").write_text("value = 2\n", encoding="utf-8")

    dirty = inspect_repository_state(repository)

    assert clean.dirty is False
    assert dirty.dirty is True
    assert dirty.workspace_hash != clean.workspace_hash


def test_large_same_size_source_change_updates_workspace_hash(tmp_path):
    repository = tmp_path / "large-source"
    repository.mkdir()
    source = repository / "large.py"
    source.write_text("a" * 1_100_000, encoding="utf-8")
    first = inspect_repository_state(repository)
    source.write_text("b" * 1_100_000, encoding="utf-8")

    second = inspect_repository_state(repository)

    assert first.workspace_hash != second.workspace_hash


def test_descendant_holding_pipes_cannot_bypass_indexer_timeout(tmp_path):
    if not hasattr(os, "fork"):
        pytest.skip("Process-group timeout test requires POSIX fork.")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        ScipIndexingService._run(
            [
                sys.executable,
                "-c",
                "import os,time; pid=os.fork(); "
                "time.sleep(5) if pid == 0 else os._exit(0)",
            ],
            cwd=tmp_path,
            capture_limit=100,
            timeout_seconds=1,
        )
    assert time.monotonic() - started < 3


def test_incomplete_workspace_fingerprint_blocks_snapshot_reuse(
    db, tmp_path, monkeypatch
):
    task, _ = _task(db, tmp_path, monkeypatch)
    monkeypatch.setattr(
        "sacm.core.code_intelligence_service.MAX_FINGERPRINT_FILES",
        1,
    )

    context = ApplicationContextService(db).build(task.id)

    metadata = context.repositories[0].scan_metadata["code_intelligence"]
    assert metadata["workspace_complete"] is False
    assert metadata["status"] == "TRUNCATED"
    assert metadata["errors"] == ["workspace_fingerprint_truncated"]
    assert not any(node.type == "semantic_symbol" for node in context.graph.nodes)


def test_same_size_indexer_config_change_updates_workspace_hash(tmp_path):
    repository = tmp_path / "configured-source"
    repository.mkdir()
    (repository / "app.ts").write_text(
        "export const value = 1;\n", encoding="utf-8"
    )
    config = repository / "tsconfig.json"
    config.write_text('{"include":["src"]}', encoding="utf-8")
    first = inspect_repository_state(repository)
    config.write_text('{"exclude":["src"]}', encoding="utf-8")

    second = inspect_repository_state(repository)

    assert first.workspace_hash != second.workspace_hash
