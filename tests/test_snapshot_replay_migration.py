from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


SNAPSHOT_TABLES = {"run_snapshots", "run_replays"}


def test_snapshot_replay_migration_supports_fresh_database(tmp_path, monkeypatch):
    database = tmp_path / "fresh-snapshots.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    command.upgrade(_config(), "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    assert SNAPSHOT_TABLES <= set(inspector.get_table_names())
    assert {
        column["name"] for column in inspector.get_columns("run_snapshots")
    } >= {
        "run_id",
        "task_id",
        "event_sequence",
        "event_hash",
        "workflow_version",
        "run_state",
        "step_state",
        "checksum",
        "parent_snapshot_id",
        "creation_reason",
    }


def test_snapshot_replay_migration_supports_existing_d2_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing-snapshots.db"
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
                "VALUES ('d2f6c8a1e4b7')"
            )
        )
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    inspector = inspect(engine)
    assert SNAPSHOT_TABLES <= set(inspector.get_table_names())
    assert {
        column["name"] for column in inspector.get_columns("run_replays")
    } >= {
        "source_run_id",
        "source_snapshot_id",
        "replay_run_id",
        "overrides",
        "replay_reason",
    }
