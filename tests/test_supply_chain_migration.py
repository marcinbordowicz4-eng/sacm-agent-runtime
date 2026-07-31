from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


TABLES = {
    "supply_chain_images",
    "supply_chain_releases",
    "supply_chain_records",
    "supply_chain_attestations",
}


def test_supply_chain_migration_supports_fresh_database(tmp_path, monkeypatch):
    database = tmp_path / "fresh-supply-chain.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    command.upgrade(_config(), "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    assert TABLES <= set(inspector.get_table_names())
    assert {"supply_chain_status", "missing_supply_chain_evidence"} <= {
        column["name"] for column in inspector.get_columns("runs")
    }
    assert {
        "signature_algorithm",
        "public_key_fingerprint",
        "verification_status",
        "pack_hash",
    } <= {
        column["name"] for column in inspector.get_columns("evidence_packs")
    }


def test_supply_chain_migration_is_dynamic_after_enterprise_secrets(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing-supply-chain.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        for statement in (
            "CREATE TABLE runs (id VARCHAR PRIMARY KEY)",
            "CREATE TABLE evidence_packs (id VARCHAR PRIMARY KEY, run_id VARCHAR)",
            "CREATE TABLE artifacts (id VARCHAR PRIMARY KEY)",
            "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)",
            "INSERT INTO alembic_version VALUES ('e6a9b4c5d7f1')",
        ):
            connection.execute(text(statement))
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    assert TABLES <= set(inspect(engine).get_table_names())
