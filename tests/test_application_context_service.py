import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from sacm.core.application_context_service import ApplicationContextService
from sacm.core.task_intake_service import TaskIntakeService
from sacm.infrastructure.db.models import (
    ApplicationContext,
    ApplicationContextRepository,
    Base,
    Run,
)
from sacm.infrastructure.db.session import get_db
from sacm.schemas.task import RepositoryReference, TaskContractV1


def _write_repositories(root):
    backend = root / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text(
        """
[project]
name = "orders"
dependencies = ["fastapi>=0.110", "sqlalchemy>=2"]
""".strip()
    )
    (backend / "app").mkdir()
    (backend / "app" / "__init__.py").write_text("")
    (backend / "app" / "api.py").write_text(
        """
from fastapi import APIRouter

router = APIRouter()

@router.post("/orders")
def create_order():
    return {}
""".strip()
    )
    (backend / "app" / "models.py").write_text(
        """
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class Order(Base):
    __tablename__ = "orders"
""".strip()
    )

    frontend = root / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"react": "19.0.0"},
                "devDependencies": {"typescript": "5.0.0"},
            }
        )
    )
    (frontend / "src").mkdir()
    (frontend / "src" / "orders.ts").write_text(
        "export const ordersEndpoint = '/orders';"
    )
    return backend, frontend


def test_builds_durable_deterministic_multi_repository_context(
    db, tmp_path, monkeypatch
):
    backend, frontend = _write_repositories(tmp_path)
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "SACM_GITHUB_REPOSITORIES_JSON",
        json.dumps({"acme/frontend": str(frontend)}),
    )
    contract = TaskContractV1(
        connector_type="github",
        external_id="acme/orders#12",
        title="Update orders API and database schema",
        description="Change the orders endpoint and SQLAlchemy model.",
        acceptance_criteria=["The React client can create orders."],
        repositories=[
            RepositoryReference(path=str(backend), base_revision="main"),
            RepositoryReference(full_name="acme/frontend"),
        ],
        requested_by="platform",
    )
    task, _, _ = TaskIntakeService(db).ingest(contract)
    service = ApplicationContextService(db)

    first = service.build(task.id)
    second = service.build(task.id)

    assert first.status == "complete"
    assert first.scanner_version == "deterministic-scanner/v2"
    assert [item.status for item in first.repositories] == ["available", "available"]
    assert {node.type for node in first.graph.nodes} >= {
        "api_route",
        "database_schema",
        "dependency",
        "dependency_manifest",
        "file",
        "module",
        "repository",
    }
    assert any(node.label == "POST /orders" for node in first.graph.nodes)
    assert any(node.label == "orders" for node in first.graph.nodes)
    assert first.graph_hash == second.graph_hash
    assert first.graph == second.graph
    assert first.impact_analysis == second.impact_analysis
    assert first.risk_analysis == second.risk_analysis
    assert first.risk_analysis.score > 0
    assert first.impact_analysis.impacted_nodes
    assert {factor.code for factor in first.risk_analysis.factors} >= {
        "api_change",
        "database_change",
        "dependency_change",
        "cross_repository_change",
    }
    assert db.query(ApplicationContext).count() == 1
    assert db.query(ApplicationContextRepository).count() == 2


def test_unavailable_and_unsafe_repositories_are_explicit(
    db, tmp_path, monkeypatch
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(allowed))
    task, _, _ = TaskIntakeService(db).ingest(
        TaskContractV1(
            connector_type="generic",
            external_id="unavailable-repositories",
            title="Inspect repositories",
            description="Build context even when repositories are unavailable.",
            acceptance_criteria=["Unavailable repositories are reported."],
            repositories=[
                RepositoryReference(path=str(blocked)),
                RepositoryReference(full_name="acme/missing"),
            ],
            requested_by="platform",
        )
    )
    context = ApplicationContextService(db).build(task.id)

    assert context.status == "unavailable"
    assert [item.error_code for item in context.repositories] == [
        "repository_path_invalid",
        "repository_unavailable",
    ]
    assert all(item.error_message for item in context.repositories)
    assert {
        node.metadata["status"]
        for node in context.graph.nodes
        if node.type == "repository"
    } == {"unavailable"}
    assert any(
        factor.code == "unavailable_repositories"
        for factor in context.risk_analysis.factors
    )


def test_scanner_skips_generated_huge_and_escaping_symlink_files(
    db, tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    repository.mkdir()
    generated = repository / "generated"
    generated.mkdir()
    (generated / "routes.py").write_text(
        "@app.get('/generated')\ndef generated(): pass"
    )
    (repository / "huge.py").write_text("x" * 1_000_001)
    outside = tmp_path / "outside.py"
    outside.write_text("@app.get('/outside')\ndef outside(): pass")
    (repository / "linked.py").symlink_to(outside)
    (repository / "app.py").write_text("@app.get('/safe')\ndef safe(): pass")
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    task, _, _ = TaskIntakeService(db).ingest(
        TaskContractV1(
            connector_type="generic",
            external_id="bounded-scanner",
            title="Inspect the safe API route",
            description="Build bounded repository context.",
            acceptance_criteria=["Only repository-owned source is analyzed."],
            repositories=[RepositoryReference(path=str(repository))],
            requested_by="platform",
        )
    )

    context = ApplicationContextService(db).build(task.id)

    paths = {node.path for node in context.graph.nodes}
    assert "generated/routes.py" not in paths
    assert "linked.py" not in paths
    huge = next(node for node in context.graph.nodes if node.path == "huge.py")
    assert huge.metadata["content_skipped"] == "oversized"
    assert any(node.label == "GET /safe" for node in context.graph.nodes)
    assert not any(node.label in {"GET /outside", "GET /generated"} for node in context.graph.nodes)


def test_application_context_api_is_authenticated_and_returns_impact(
    tmp_path, monkeypatch
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    backend, _ = _write_repositories(tmp_path)
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(tmp_path))
    task, _, _ = TaskIntakeService(db).ingest(
        TaskContractV1(
            connector_type="generic",
            external_id="api-context",
            title="Update orders API",
            description="Change the orders route.",
            acceptance_criteria=["POST /orders remains available."],
            repositories=[RepositoryReference(path=str(backend))],
            requested_by="platform",
        )
    )
    db.add(Run(id="run-api", task_id=task.id))
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            path = f"/v1/tasks/{task.id}/application-context"
            assert client.post(path).status_code == 401
            built = client.post(path, headers={"X-SACM-Actor": "developer"})
            assert built.status_code == 201
            assert client.get(
                path, headers={"X-SACM-Actor": "developer"}
            ).status_code == 200
            impact = client.get(
                f"{path}/impact-risk",
                headers={"X-SACM-Actor": "developer"},
            )
            assert impact.status_code == 200
            assert impact.json()["graph_hash"] == built.json()["graph_hash"]
            package_path = f"/v1/tasks/{task.id}/context-package"
            package = client.post(
                package_path,
                headers={"X-SACM-Actor": "developer"},
                json={
                    "run_id": "run-api",
                    "role": "coder",
                    "failing_symbols": ["create_order"],
                },
            )
            assert package.status_code == 201
            assert package.json()["schema_version"] == "context-package/v2"
            latest = client.get(
                f"{package_path}?run_id=run-api",
                headers={"X-SACM-Actor": "developer"},
            )
            assert latest.status_code == 200
            assert latest.json()["package_hash"] == package.json()["package_hash"]
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
