from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


TABLES = {
    "backup_records",
    "disaster_recovery_drills",
    "slo_contracts",
    "slo_evaluations",
    "operational_health_snapshots",
}


def test_resilience_migration_supports_fresh_database(tmp_path, monkeypatch):
    database = tmp_path / "fresh-resilience.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    inspector = inspect(create_engine(url))
    assert TABLES <= set(inspector.get_table_names())
    assert {
        "recovery_count",
        "last_recovery_reason",
        "dead_lettered_at",
    } <= {column["name"] for column in inspector.get_columns("execution_jobs")}


def test_resilience_migration_is_dynamic_after_supply_chain(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing-resilience.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES ('f7b0c5d6e8a2')")
        )
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    assert TABLES <= set(inspect(engine).get_table_names())
