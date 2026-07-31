"""Add enterprise tenant IAM, service credentials, and audit chain.

Revision ID: d5f8a2b3c4e6
Revises: c4e7f1a2b3d5
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5f8a2b3c4e6"
down_revision: Union[str, Sequence[str], None] = "c4e7f1a2b3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT_TABLES = {
    "tasks": True,
    "context_events": True,
    "memory_chunks": True,
    "artifacts": True,
    "runs": False,
    "run_snapshots": True,
    "run_replays": True,
    "evidence_packs": True,
    "approvals": True,
    "execution_plans": True,
}


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())

    for table_name, add_project in _TENANT_TABLES.items():
        if table_name not in tables:
            continue
        _add_column(table_name, sa.Column("organization_id", sa.String(), nullable=True))
        if add_project:
            _add_column(table_name, sa.Column("project_id", sa.String(), nullable=True))
        _add_column(table_name, sa.Column("tenant_attribution", sa.JSON(), nullable=True))
        _index(table_name, "organization_id")
        _foreign_key(table_name, "organization_id", "organizations", "id")
        if add_project:
            _index(table_name, "project_id")
            _foreign_key(table_name, "project_id", "projects", "id")

    if "memberships" in tables:
        _add_column("memberships", sa.Column("permissions", sa.JSON(), nullable=True))

    for table_name in (
        "executor_enrollment_tokens",
        "executor_registrations",
        "execution_jobs",
    ):
        if table_name in tables:
            _add_column(
                table_name,
                sa.Column("tenant_attribution", sa.JSON(), nullable=True),
            )

    tables = set(sa.inspect(connection).get_table_names())
    if "service_credentials" not in tables:
        op.create_table(
            "service_credentials",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("token_hash", sa.String(), nullable=False),
            sa.Column("token_prefix", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("permissions", sa.JSON(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "organization_id", "name", name="uq_service_credentials_org_name"
            ),
            sa.UniqueConstraint(
                "token_hash", name="uq_service_credentials_token_hash"
            ),
        )
        for column in ("organization_id", "project_id"):
            _index("service_credentials", column)

    tables = set(sa.inspect(connection).get_table_names())
    if "tenant_audit_events" not in tables:
        op.create_table(
            "tenant_audit_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("actor_id", sa.String(), nullable=False),
            sa.Column("actor_type", sa.String(), nullable=False),
            sa.Column("service_credential_id", sa.String(), nullable=True),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("resource_type", sa.String(), nullable=False),
            sa.Column("resource_id", sa.String(), nullable=True),
            sa.Column("decision", sa.String(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("correlation_id", sa.String(), nullable=True),
            sa.Column("request_metadata", sa.JSON(), nullable=False),
            sa.Column("previous_event_hash", sa.String(), nullable=True),
            sa.Column("event_hash", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(
                ["service_credential_id"], ["service_credentials.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "organization_id",
                "sequence",
                name="uq_tenant_audit_org_sequence",
            ),
        )
        for column in (
            "organization_id",
            "project_id",
            "actor_id",
            "service_credential_id",
            "action",
            "resource_type",
            "decision",
            "correlation_id",
        ):
            _index("tenant_audit_events", column)

    _backfill_bridged_tenant_ids(connection)


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in ("tenant_audit_events", "service_credentials"):
        if table_name in tables:
            op.drop_table(table_name)
    for table_name in (
        "executor_enrollment_tokens",
        "executor_registrations",
        "execution_jobs",
    ):
        if table_name in tables:
            _drop_column(table_name, "tenant_attribution")
    if "memberships" in tables:
        _drop_column("memberships", "permissions")
    for table_name, add_project in reversed(tuple(_TENANT_TABLES.items())):
        if table_name not in tables:
            continue
        _drop_column(table_name, "tenant_attribution")
        if add_project:
            _drop_column(table_name, "project_id")
        _drop_column(table_name, "organization_id")


def _add_column(table_name: str, column: sa.Column) -> None:
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    if column.name not in columns:
        op.add_column(table_name, column)


def _drop_column(table_name: str, column_name: str) -> None:
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    if column_name in columns:
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column(column_name)


def _index(table_name: str, column_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes(table_name)}
    name = op.f(f"ix_{table_name}_{column_name}")
    if name not in indexes:
        op.create_index(name, table_name, [column_name], unique=False)


def _foreign_key(
    table_name: str,
    column_name: str,
    referred_table: str,
    referred_column: str,
) -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        return
    tables = set(sa.inspect(connection).get_table_names())
    if referred_table not in tables:
        return
    foreign_keys = sa.inspect(connection).get_foreign_keys(table_name)
    if any(
        item.get("constrained_columns") == [column_name]
        for item in foreign_keys
    ):
        return
    op.create_foreign_key(
        f"fk_{table_name}_{column_name}_{referred_table}",
        table_name,
        referred_table,
        [column_name],
        [referred_column],
    )


def _backfill_bridged_tenant_ids(connection: sa.Connection) -> None:
    if _has_columns(connection, "runs", "organization_id", "project_id") and _has_columns(
        connection, "projects", "id", "organization_id"
    ):
        connection.execute(
            sa.text(
                "UPDATE runs SET organization_id = "
                "(SELECT projects.organization_id FROM projects "
                "WHERE projects.id = runs.project_id) "
                "WHERE organization_id IS NULL AND project_id IS NOT NULL"
            )
        )
    if _has_columns(connection, "tasks", "id", "project_id") and _has_columns(
        connection, "runs", "task_id", "project_id"
    ):
        connection.execute(
            sa.text(
                "UPDATE tasks SET project_id = "
                "(SELECT MIN(runs.project_id) FROM runs "
                "WHERE runs.task_id = tasks.id AND runs.project_id IS NOT NULL) "
                "WHERE project_id IS NULL AND "
                "(SELECT COUNT(DISTINCT runs.project_id) FROM runs "
                "WHERE runs.task_id = tasks.id AND runs.project_id IS NOT NULL) = 1"
            )
        )
    if _has_columns(connection, "tasks", "organization_id", "project_id") and _has_columns(
        connection, "projects", "id", "organization_id"
    ):
        connection.execute(
            sa.text(
                "UPDATE tasks SET organization_id = "
                "(SELECT projects.organization_id FROM projects "
                "WHERE projects.id = tasks.project_id) "
                "WHERE organization_id IS NULL AND project_id IS NOT NULL"
            )
        )

    for table_name in (
        "context_events",
        "memory_chunks",
        "artifacts",
        "execution_plans",
    ):
        if (
            _has_columns(
                connection,
                table_name,
                "organization_id",
                "project_id",
                "task_id",
            )
            and _has_columns(
                connection, "tasks", "id", "organization_id", "project_id"
            )
        ):
            connection.execute(
                sa.text(
                    f"UPDATE {table_name} SET "
                    "organization_id = (SELECT tasks.organization_id FROM tasks "
                    f"WHERE tasks.id = {table_name}.task_id), "
                    "project_id = (SELECT tasks.project_id FROM tasks "
                    f"WHERE tasks.id = {table_name}.task_id) "
                    "WHERE organization_id IS NULL"
                )
            )

    for table_name in (
        "run_snapshots",
        "evidence_packs",
        "approvals",
        "execution_jobs",
    ):
        if (
            _has_columns(
                connection,
                table_name,
                "organization_id",
                "project_id",
                "run_id",
            )
            and _has_columns(
                connection, "runs", "id", "organization_id", "project_id"
            )
        ):
            connection.execute(
                sa.text(
                    f"UPDATE {table_name} SET "
                    "organization_id = (SELECT runs.organization_id FROM runs "
                    f"WHERE runs.id = {table_name}.run_id), "
                    "project_id = (SELECT runs.project_id FROM runs "
                    f"WHERE runs.id = {table_name}.run_id) "
                    "WHERE organization_id IS NULL"
                )
            )

    if _has_columns(
        connection,
        "run_replays",
        "organization_id",
        "project_id",
        "source_run_id",
    ) and _has_columns(connection, "runs", "id", "organization_id", "project_id"):
        connection.execute(
            sa.text(
                "UPDATE run_replays SET "
                "organization_id = (SELECT runs.organization_id FROM runs "
                "WHERE runs.id = run_replays.source_run_id), "
                "project_id = (SELECT runs.project_id FROM runs "
                "WHERE runs.id = run_replays.source_run_id) "
                "WHERE organization_id IS NULL"
            )
        )
    for table_name in ("execution_jobs",):
        if _has_columns(
            connection, table_name, "organization_id", "project_id"
        ) and _has_columns(connection, "projects", "id", "organization_id"):
            connection.execute(
                sa.text(
                    f"UPDATE {table_name} SET organization_id = "
                    "(SELECT projects.organization_id FROM projects "
                    f"WHERE projects.id = {table_name}.project_id) "
                    "WHERE organization_id IS NULL AND project_id IS NOT NULL"
                )
            )


def _has_columns(
    connection: sa.Connection, table_name: str, *column_names: str
) -> bool:
    if table_name not in sa.inspect(connection).get_table_names():
        return False
    columns = {
        item["name"] for item in sa.inspect(connection).get_columns(table_name)
    }
    return set(column_names).issubset(columns)
