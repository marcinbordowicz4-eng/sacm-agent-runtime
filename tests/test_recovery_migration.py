from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


RECOVERY_COLUMNS = {
    "recovery_state",
    "recovery_attempt_count",
    "last_failure_classification",
    "last_recovery_action",
}


def test_recovery_migration_supports_fresh_database(tmp_path, monkeypatch):
    database = tmp_path / "fresh-recovery.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    columns = {
        column["name"]
        for column in inspect(create_engine(url)).get_columns("runs")
    }
    assert RECOVERY_COLUMNS <= columns


def test_recovery_migration_supports_existing_release_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing-recovery.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
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
                "VALUES ('c8d1e4f7a2b5')"
            )
        )
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    assert RECOVERY_COLUMNS <= {
        column["name"] for column in inspect(engine).get_columns("runs")
    }
