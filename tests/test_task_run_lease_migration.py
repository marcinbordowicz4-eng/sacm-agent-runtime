from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_task_run_lease_migration_creates_durable_lock_table(
    tmp_path, monkeypatch
):
    database = tmp_path / "lease-migration.db"
    database_url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(_config(database_url), "head")

    inspector = inspect(create_engine(database_url))
    assert "task_run_leases" in inspector.get_table_names()
    columns = {
        column["name"] for column in inspector.get_columns("task_run_leases")
    }
    assert columns == {
        "task_id",
        "owner_token",
        "acquired_at",
        "heartbeat_at",
        "expires_at",
    }
