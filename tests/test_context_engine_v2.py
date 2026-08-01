import hashlib

from sacm.agents.claude_reasoner import ClaudeReasonerAgent
from sacm.core.application_context_service import ApplicationContextService
from sacm.core.context_compiler import ContextCompiler
from sacm.core.context_engine_service import ContextEngineService
from sacm.core.task_intake_service import TaskIntakeService
from sacm.infrastructure.db.models import ContextEvent, Run
from sacm.schemas.application_context import ContextExpansionRequest
from sacm.schemas.task import RepositoryReference, TaskContractV1


def _task_with_graph(db, tmp_path, monkeypatch):
    repository = tmp_path / "orders"
    repository.mkdir()
    (repository / "app.py").write_text(
        """
def validate_order(order):
    return bool(order)

def create_order(order):
    if not validate_order(order):
        raise ValueError("invalid")
    return {"created": True}
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
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    task, _, _ = TaskIntakeService(db).ingest(
        TaskContractV1(
            connector_type="generic",
            external_id="context-engine-v2",
            title="Fix order validation",
            description="Repair create_order and its focused regression test.",
            acceptance_criteria=["Invalid orders are rejected."],
            repositories=[RepositoryReference(path=str(repository))],
            requested_by="platform",
        )
    )
    return task


def test_application_graph_contains_symbols_calls_and_tests(db, tmp_path, monkeypatch):
    task = _task_with_graph(db, tmp_path, monkeypatch)

    context = ApplicationContextService(db).build(task.id)

    assert context.scanner_version == "deterministic-scanner/v2.1"
    symbols = {node.label: node for node in context.graph.nodes}
    assert symbols["create_order"].type == "symbol"
    assert symbols["test_create_order"].type == "test_symbol"
    typed_edges = {
        (edge.source, edge.target, edge.type) for edge in context.graph.edges
    }
    create_id = symbols["create_order"].id
    validate_id = symbols["validate_order"].id
    test_id = symbols["test_create_order"].id
    assert (create_id, validate_id, "calls") in typed_edges
    assert (test_id, create_id, "tests") in typed_edges


def test_context_package_traverses_and_persists_evidence(
    db, tmp_path, monkeypatch
):
    task = _task_with_graph(db, tmp_path, monkeypatch)
    ApplicationContextService(db).build(task.id)
    db.add(Run(id="run-1", task_id=task.id))
    db.commit()

    package = ContextEngineService(db).build(
        task.id,
        ContextExpansionRequest(
            run_id="run-1",
            step_id="agent-2",
            role="coder",
            reason="recovery_context_expansion",
            failing_symbols=["validate_order"],
            affected_requirements=["Invalid orders are rejected."],
            max_depth=3,
            max_nodes=20,
        ),
    )

    labels = {node.label for node in package.nodes}
    assert {"validate_order", "create_order", "test_create_order"} <= labels
    assert package.seed_node_ids
    assert any(edge.type == "calls" for edge in package.edges)
    assert any(edge.type == "tests" for edge in package.edges)
    assert package.files
    app_excerpt = next(item for item in package.files if item.path == "app.py")
    assert "def validate_order" in app_excerpt.content
    full_content = (tmp_path / "orders" / "app.py").read_text(encoding="utf-8")
    assert app_excerpt.content_hash == hashlib.sha256(
        full_content.encode()
    ).hexdigest()
    assert len(package.package_hash) == 64

    event = (
        db.query(ContextEvent)
        .filter_by(task_id=task.id, event_type="context_package_v2")
        .one()
    )
    assert event.payload["run_id"] == "run-1"
    assert all(
        item["content"] == "" and item["content_included"] is False
        for item in event.payload["files"]
    )
    latest = ContextEngineService(db).latest(task.id, run_id="run-1")
    assert latest is not None
    assert latest.package_hash == package.package_hash
    assert all(not item.content_included for item in latest.files)


def test_context_package_refreshes_graph_after_workspace_change(
    db, tmp_path, monkeypatch
):
    task = _task_with_graph(db, tmp_path, monkeypatch)
    initial = ApplicationContextService(db).build(task.id)
    app_path = tmp_path / "orders" / "app.py"
    app_path.write_text(
        app_path.read_text(encoding="utf-8")
        + "\n\ndef normalize_order(order):\n    return order\n",
        encoding="utf-8",
    )

    package = ContextEngineService(db).build(
        task.id,
        ContextExpansionRequest(failing_symbols=["normalize_order"]),
    )

    assert package.graph_hash != initial.graph_hash
    assert any(node.label == "normalize_order" for node in package.nodes)


def test_context_package_does_not_read_oversized_graph_files(
    db, tmp_path, monkeypatch
):
    task = _task_with_graph(db, tmp_path, monkeypatch)
    huge_path = tmp_path / "orders" / "huge.py"
    huge_path.write_text("x" * 1_000_001, encoding="utf-8")

    package = ContextEngineService(db).build(
        task.id,
        ContextExpansionRequest(changed_files=["huge.py"]),
    )

    assert any(node.path == "huge.py" for node in package.nodes)
    assert all(excerpt.path != "huge.py" for excerpt in package.files)


def test_context_compiler_includes_package_files(db, tmp_path, monkeypatch):
    task = _task_with_graph(db, tmp_path, monkeypatch)
    ApplicationContextService(db).build(task.id)
    package = ContextEngineService(db).build(
        task.id,
        ContextExpansionRequest(failing_symbols=["create_order"]),
    )

    context = ContextCompiler().compile(
        task=task,
        agent=ClaudeReasonerAgent(),
        history=[],
        memory=[],
        context_package=package,
    )

    assert context.context_package is not None
    assert context.context_package["package_hash"] == package.package_hash
    assert any("create_order" in content for content in context.files.values())
    assert any(package.package_hash in item for item in context.constraints)
    contract = ContextCompiler().compile_v1(
        run_id="legacy:test",
        step_id="agent-1",
        agent=ClaudeReasonerAgent(),
        context=context,
    )
    restored = ClaudeReasonerAgent()._context_from_v1(contract)
    assert restored.context_package is not None
    assert restored.context_package["package_hash"] == package.package_hash


def test_context_package_rejects_cross_task_run(db, tmp_path, monkeypatch):
    first = _task_with_graph(db, tmp_path, monkeypatch)
    second, _, _ = TaskIntakeService(db).ingest(
        TaskContractV1(
            connector_type="generic",
            external_id="other-task",
            title="Other task",
            description="Separate tenant-scoped work.",
            acceptance_criteria=["No cross-task provenance."],
            repositories=[],
            requested_by="platform",
        )
    )
    db.add(Run(id="other-run", task_id=second.id))
    db.commit()

    try:
        ContextEngineService(db).build(
            first.id,
            ContextExpansionRequest(run_id="other-run"),
        )
    except ValueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("Cross-task run provenance was accepted.")
