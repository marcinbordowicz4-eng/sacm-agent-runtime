from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


def test_application_context_migration_supports_fresh_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "fresh.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    command.upgrade(_config(), "head")

    tables = set(inspect(create_engine(f"sqlite:///{database}")).get_table_names())
    assert "application_contexts" in tables
    assert "application_context_repositories" in tables


def test_application_context_migration_supports_existing_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing.db"
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
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL PRIMARY KEY"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('7f44d635cf1a')"
            )
        )
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    inspector = inspect(engine)
    assert "application_contexts" in inspector.get_table_names()
    assert "application_context_repositories" in inspector.get_table_names()
    assert {
        column["name"]
        for column in inspector.get_columns("application_contexts")
    } >= {"task_id", "graph", "impact_analysis", "risk_analysis"}
