from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_lifecycle_metric_migration_creates_durable_metrics(tmp_path, monkeypatch):
    database = tmp_path / "lifecycle-metric-migration.db"
    database_url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(_config(database_url), "head")

    inspector = inspect(create_engine(database_url))
    assert "lifecycle_metrics" in inspector.get_table_names()
    assert {
        "id",
        "task_id",
        "run_id",
        "metric",
        "value",
        "details",
        "created_at",
    } == {
        column["name"] for column in inspector.get_columns("lifecycle_metrics")
    }
