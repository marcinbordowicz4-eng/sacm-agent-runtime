from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_workflow_job_migration_creates_durable_queue(tmp_path, monkeypatch):
    database = tmp_path / "workflow-job-migration.db"
    database_url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(_config(database_url), "head")

    inspector = inspect(create_engine(database_url))
    assert "workflow_jobs" in inspector.get_table_names()
    columns = {
        column["name"] for column in inspector.get_columns("workflow_jobs")
    }
    assert {
        "id",
        "run_id",
        "state",
        "attempt",
        "max_attempts",
        "available_at",
        "lease_token",
        "lease_expires_at",
        "last_error",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    } == columns
