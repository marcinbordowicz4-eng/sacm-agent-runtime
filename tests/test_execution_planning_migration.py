from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config():
    config = Config()
    config.set_main_option("script_location", "sacm/migrations")
    return config


PLANNING_TABLES = {
    "execution_plans",
    "execution_plan_steps",
    "execution_plan_risk_decisions",
    "execution_plan_policy_decisions",
    "execution_plan_security_reviews",
    "execution_plan_secret_requirements",
    "execution_plan_approval_gates",
}


def test_execution_planning_migration_supports_fresh_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "fresh-planning.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    command.upgrade(_config(), "head")

    tables = set(inspect(create_engine(f"sqlite:///{database}")).get_table_names())
    assert PLANNING_TABLES <= tables


def test_execution_planning_migration_supports_existing_b9_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing-planning.db"
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
                "CREATE TABLE application_contexts ("
                "id VARCHAR NOT NULL PRIMARY KEY, "
                "task_id VARCHAR NOT NULL"
                ")"
            )
        )
        connection.execute(
            text("CREATE TABLE approvals (id VARCHAR NOT NULL PRIMARY KEY)")
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
                "VALUES ('b9c4e201a6d8')"
            )
        )
    monkeypatch.setenv("DATABASE_URL", url)

    command.upgrade(_config(), "head")

    inspector = inspect(engine)
    assert PLANNING_TABLES <= set(inspector.get_table_names())
    assert {
        column["name"] for column in inspector.get_columns("execution_plans")
    } >= {
        "task_id",
        "application_context_id",
        "revision",
        "source_hash",
        "policy_pack",
    }
