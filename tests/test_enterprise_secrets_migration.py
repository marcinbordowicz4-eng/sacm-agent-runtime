from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


def test_enterprise_secrets_migration_fresh_database(tmp_path, monkeypatch):
    database = tmp_path / "enterprise-secrets.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    inspector = inspect(create_engine(url))
    assert {
        "secret_provider_configs",
        "credential_leases",
    } <= set(inspector.get_table_names())
    lease_columns = {
        item["name"] for item in inspector.get_columns("credential_leases")
    }
    assert {
        "organization_id",
        "project_id",
        "task_id",
        "run_id",
        "job_id",
        "executor_id",
        "requested_permissions",
        "resource",
        "provider",
        "opaque_handle",
        "issued_at",
        "expires_at",
        "revoked_at",
        "last_used_at",
        "use_count",
        "audience",
        "policy_decision",
        "provider_lease_id_hash",
    } <= lease_columns
    assert not {
        "value",
        "secret_value",
        "credential_value",
        "provider_lease_id",
    }.intersection(lease_columns)
    assert "secret_requirements" in {
        item["name"] for item in inspector.get_columns("execution_jobs")
    }


def test_enterprise_secrets_migration_accepts_dynamic_d5_baseline(
    tmp_path, monkeypatch
):
    database = tmp_path / "dynamic-d5.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES ('d5f8a2b3c4e6')")
        )
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    assert {
        "secret_provider_configs",
        "credential_leases",
    } <= set(inspect(engine).get_table_names())
