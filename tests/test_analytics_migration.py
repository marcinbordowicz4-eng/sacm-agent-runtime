from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


ANALYTICS_TABLES = {
    "run_outcome_analytics",
    "run_step_outcome_analytics",
    "agent_outcome_analytics",
}


def test_analytics_migration_supports_fresh_database(tmp_path, monkeypatch):
    database = tmp_path / "fresh-analytics.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    command.upgrade(_config(), "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    assert ANALYTICS_TABLES <= set(inspector.get_table_names())
    assert {
        column["name"]
        for column in inspector.get_columns("run_outcome_analytics")
    } >= {
        "outcome",
        "latency_ms",
        "input_tokens",
        "estimated_cost_usd",
        "evidence_coverage_percent",
        "requirement_coverage_percent",
        "policy_blocked",
        "source_fingerprint",
    }


def test_analytics_migration_supports_existing_f8_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing-analytics.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE tasks ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "title VARCHAR NOT NULL, "
                "description TEXT NOT NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE projects ("
                "id VARCHAR NOT NULL PRIMARY KEY"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE runs ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "task_id VARCHAR NOT NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE run_steps ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "run_id VARCHAR NOT NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL PRIMARY KEY"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('f8b2c3d4e5f6')"
            )
        )
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    assert ANALYTICS_TABLES <= set(inspect(engine).get_table_names())
