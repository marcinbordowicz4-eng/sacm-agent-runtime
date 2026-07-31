from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


TRACEABILITY_TABLES = {"requirements", "requirement_links"}


def test_traceability_migration_supports_fresh_database(tmp_path, monkeypatch):
    database = tmp_path / "fresh-traceability.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    command.upgrade(_config(), "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    assert TRACEABILITY_TABLES <= set(inspector.get_table_names())
    assert {
        column["name"] for column in inspector.get_columns("requirement_links")
    } >= {
        "requirement_id",
        "run_id",
        "target_type",
        "target_id",
        "relation",
        "source",
        "metadata",
    }


def test_traceability_migration_supports_existing_e7_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing-traceability.db"
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
                "CREATE TABLE runs ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "task_id VARCHAR NOT NULL"
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
                "VALUES ('e7a1b2c3d4f5')"
            )
        )
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    inspector = inspect(engine)
    assert TRACEABILITY_TABLES <= set(inspector.get_table_names())
