from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


def test_jira_migration_is_after_current_head_and_supports_fresh_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "jira-e2e.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(_config(), "head")
    inspector = inspect(create_engine(url))
    assert {
        "jira_connectors",
        "jira_webhook_deliveries",
        "jira_connector_operations",
        "jira_delivery_states",
    } <= set(inspector.get_table_names())
    assert {
        column["name"]
        for column in inspector.get_columns("jira_connector_operations")
    } >= {
        "idempotency_key",
        "payload_hash",
        "attempt_count",
        "status",
        "error",
    }


def test_jira_migration_supports_existing_current_head(tmp_path, monkeypatch):
    database = tmp_path / "jira-existing-head.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        for table in ("organizations", "projects", "tasks", "runs"):
            connection.execute(
                text(f"CREATE TABLE {table} (id VARCHAR NOT NULL PRIMARY KEY)")
            )
        connection.execute(
            text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('b2e5f8a1c3d7')"
            )
        )
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(_config(), "head")
    assert "jira_delivery_states" in inspect(engine).get_table_names()
