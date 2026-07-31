from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


TABLES = {
    "data_governance_policies",
    "data_governance_policy_rules",
    "governance_legal_holds",
    "governance_requests",
    "governance_request_items",
    "audit_export_batches",
    "siem_sinks",
    "siem_deliveries",
}


def test_governance_migration_supports_fresh_database(tmp_path, monkeypatch):
    database = tmp_path / "fresh-governance.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    inspector = inspect(create_engine(url))
    assert TABLES <= set(inspector.get_table_names())
    assert {"storage_region", "storage_classification", "storage_class"} <= {
        column["name"] for column in inspector.get_columns("evidence_packs")
    }


def test_governance_migration_is_dynamic_after_resilience(tmp_path, monkeypatch):
    database = tmp_path / "existing-governance.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES ('a1d4e7f9b2c6')")
        )
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    assert TABLES <= set(inspect(engine).get_table_names())
