"""Add enterprise resilience, backup, DR, SLO, and recovery records.

Revision ID: a1d4e7f9b2c6
Revises: f7b0c5d6e8a2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1d4e7f9b2c6"
down_revision: Union[str, Sequence[str], None] = "f7b0c5d6e8a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _upgrade_execution_jobs(tables)
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _create_backup_records(tables)
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _create_dr_drills(tables)
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _create_slo_contracts(tables)
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _create_slo_evaluations(tables)
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _create_health_snapshots(tables)


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in (
        "operational_health_snapshots",
        "slo_evaluations",
        "slo_contracts",
        "disaster_recovery_drills",
        "backup_records",
    ):
        if table_name in tables:
            op.drop_table(table_name)
    if "execution_jobs" in tables:
        for name in ("dead_lettered_at", "last_recovery_reason", "recovery_count"):
            _drop_column("execution_jobs", name)


def _upgrade_execution_jobs(tables: set[str]) -> None:
    if "execution_jobs" not in tables:
        return
    _add_column(
        "execution_jobs",
        sa.Column(
            "recovery_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    _add_column(
        "execution_jobs",
        sa.Column("last_recovery_reason", sa.String(), nullable=True),
    )
    _add_column(
        "execution_jobs",
        sa.Column("dead_lettered_at", sa.DateTime(), nullable=True),
    )
    inspector = sa.inspect(op.get_bind())
    constraints = {
        item["name"] for item in inspector.get_check_constraints("execution_jobs")
    }
    if "ck_execution_job_state" in constraints:
        with op.batch_alter_table(
            "execution_jobs", reflect_kwargs={"resolve_fks": False}
        ) as batch:
            batch.drop_constraint("ck_execution_job_state", type_="check")
            batch.create_check_constraint(
                "ck_execution_job_state",
                "state IN ('QUEUED', 'LEASED', 'RUNNING', 'COMPLETED', "
                "'FAILED', 'EXPIRED', 'CANCELLED', 'DEAD_LETTER')",
            )


def _create_backup_records(tables: set[str]) -> None:
    if "backup_records" in tables:
        return
    op.create_table(
        "backup_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("backup_type", sa.String(), nullable=False),
        sa.Column("source_database", sa.String(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("checksum_algorithm", sa.String(), nullable=False),
        sa.Column("checksum", sa.String(), nullable=True),
        sa.Column("encryption_metadata", sa.JSON(), nullable=False),
        sa.Column("artifact_metadata", sa.JSON(), nullable=False),
        sa.Column("evidence_metadata", sa.JSON(), nullable=False),
        sa.Column("rpo_target_seconds", sa.Integer(), nullable=False),
        sa.Column("rto_target_seconds", sa.Integer(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("failure", sa.JSON(), nullable=True),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "scope_type IN ('GLOBAL', 'ORGANIZATION')",
            name="ck_backup_records_scope",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CORRUPT')",
            name="ck_backup_records_status",
        ),
        *_foreign_keys(tables, {"organization_id": ("organizations", "id")}),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("scope_type", "organization_id", "status"):
        _index("backup_records", column)


def _create_dr_drills(tables: set[str]) -> None:
    if "disaster_recovery_drills" in tables:
        return
    op.create_table(
        "disaster_recovery_drills",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("backup_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("target_database", sa.String(), nullable=False),
        sa.Column("isolated_target", sa.Boolean(), nullable=False),
        sa.Column("destructive_restore", sa.Boolean(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("measured_rpo_seconds", sa.Float(), nullable=True),
        sa.Column("measured_rto_seconds", sa.Float(), nullable=True),
        sa.Column("failure", sa.JSON(), nullable=True),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'PASSED', 'FAILED')",
            name="ck_disaster_recovery_drills_status",
        ),
        *_foreign_keys(
            tables,
            {
                "backup_id": ("backup_records", "id"),
                "organization_id": ("organizations", "id"),
            },
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("backup_id", "organization_id", "status"):
        _index("disaster_recovery_drills", column)


def _create_slo_contracts(tables: set[str]) -> None:
    if "slo_contracts" in tables:
        return
    op.create_table(
        "slo_contracts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("objective_percent", sa.Float(), nullable=False),
        sa.Column("threshold_seconds", sa.Float(), nullable=True),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_foreign_keys(tables, {"organization_id": ("organizations", "id")}),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_key", "metric", name="uq_slo_contract_scope_metric"
        ),
    )
    for column in ("scope_key", "organization_id", "metric"):
        _index("slo_contracts", column)


def _create_slo_evaluations(tables: set[str]) -> None:
    if "slo_evaluations" in tables:
        return
    op.create_table(
        "slo_evaluations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("contract_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("observed_percent", sa.Float(), nullable=True),
        sa.Column("total_events", sa.Integer(), nullable=False),
        sa.Column("bad_events", sa.Integer(), nullable=False),
        sa.Column("error_budget_allowed", sa.Float(), nullable=False),
        sa.Column("error_budget_remaining", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        *_foreign_keys(
            tables,
            {
                "contract_id": ("slo_contracts", "id"),
                "organization_id": ("organizations", "id"),
            },
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("contract_id", "organization_id", "metric", "status"):
        _index("slo_evaluations", column)


def _create_health_snapshots(tables: set[str]) -> None:
    if "operational_health_snapshots" in tables:
        return
    op.create_table(
        "operational_health_snapshots",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("queue_depth", sa.Integer(), nullable=False),
        sa.Column("oldest_queue_age_seconds", sa.Float(), nullable=False),
        sa.Column("active_executor_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_foreign_keys(tables, {"organization_id": ("organizations", "id")}),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("organization_id", "status"):
        _index("operational_health_snapshots", column)


def _foreign_keys(
    tables: set[str], references: dict[str, tuple[str, str]]
) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint([column], [f"{table}.{target}"])
        for column, (table, target) in references.items()
        if table in tables
    ]


def _add_column(table_name: str, column: sa.Column) -> None:
    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    if column.name not in columns:
        op.add_column(table_name, column)


def _drop_column(table_name: str, column_name: str) -> None:
    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)
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
