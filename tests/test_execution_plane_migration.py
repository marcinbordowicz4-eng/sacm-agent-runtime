from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


TABLES = {
    "executor_enrollment_tokens",
    "executor_registrations",
    "execution_jobs",
    "service_credentials",
    "tenant_audit_events",
}


def test_execution_plane_migration_supports_fresh_database(tmp_path, monkeypatch):
    database = tmp_path / "fresh-execution.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    command.upgrade(_config(), "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    assert TABLES <= set(inspector.get_table_names())
    columns = {
        column["name"] for column in inspector.get_columns("execution_jobs")
    }
    assert {
        "payload_hash",
        "lease_token_hash",
        "result_signature_metadata",
        "customer_deployment_id",
    } <= columns
    assert "sandbox_policy" in {
        column["name"]
        for column in inspector.get_columns("executor_registrations")
    }
    assert "tenant_attribution" in {
        column["name"]
        for column in inspector.get_columns("executor_registrations")
    }
    assert {"organization_id", "project_id", "tenant_attribution"} <= {
        column["name"] for column in inspector.get_columns("tasks")
    }
    assert "permissions" in {
        column["name"] for column in inspector.get_columns("memberships")
    }


def test_execution_plane_migration_supports_existing_analytics_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing-execution.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        for statement in (
            "CREATE TABLE organizations (id VARCHAR PRIMARY KEY)",
            "CREATE TABLE projects (id VARCHAR PRIMARY KEY)",
            "CREATE TABLE tasks (id VARCHAR PRIMARY KEY)",
            "CREATE TABLE runs (id VARCHAR PRIMARY KEY)",
            "CREATE TABLE run_steps (id VARCHAR PRIMARY KEY)",
            "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)",
            "INSERT INTO alembic_version VALUES ('a9c0d1e2f3b4')",
        ):
            connection.execute(text(statement))
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    assert TABLES <= set(inspect(engine).get_table_names())
