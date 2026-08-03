from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_memory_quality_migration_adds_scope_versioning_and_expiry(
    tmp_path, monkeypatch
):
    database = tmp_path / "memory-quality.db"
    database_url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(_config(database_url), "head")

    columns = {
        column["name"]
        for column in inspect(create_engine(database_url)).get_columns(
            "memory_chunks"
        )
    }
    assert {
        "scope",
        "scope_key",
        "content_hash",
        "confidence",
        "valid_until",
        "supersedes_id",
        "superseded_at",
        "updated_at",
    } <= columns
