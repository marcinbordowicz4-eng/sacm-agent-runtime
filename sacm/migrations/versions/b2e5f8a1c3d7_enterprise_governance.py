"""Add enterprise data governance, privacy, audit export, and SIEM records.

Revision ID: b2e5f8a1c3d7
Revises: a1d4e7f9b2c6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from sacm.infrastructure.db.models import Base

revision: str = "b2e5f8a1c3d7"
down_revision: Union[str, Sequence[str], None] = "a1d4e7f9b2c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = (
    "data_governance_policies",
    "data_governance_policy_rules",
    "governance_legal_holds",
    "governance_requests",
    "governance_request_items",
    "audit_export_batches",
    "siem_sinks",
    "siem_deliveries",
)

TENANT_COLUMNS: dict[str, tuple[sa.Column, ...]] = {
    "organizations": (
        sa.Column("data_region", sa.String(), nullable=True),
        sa.Column("data_classification", sa.String(), nullable=True),
        sa.Column("governance_metadata", sa.JSON(), nullable=True),
    ),
    "projects": (
        sa.Column("data_region", sa.String(), nullable=True),
        sa.Column("data_classification", sa.String(), nullable=True),
        sa.Column("governance_metadata", sa.JSON(), nullable=True),
    ),
    "tasks": (
        sa.Column("data_region", sa.String(), nullable=True),
        sa.Column("data_classification", sa.String(), nullable=True),
    ),
    "context_events": (
        sa.Column("data_region", sa.String(), nullable=True),
        sa.Column("data_classification", sa.String(), nullable=True),
    ),
    "memory_chunks": (
        sa.Column("data_region", sa.String(), nullable=True),
        sa.Column("data_classification", sa.String(), nullable=True),
    ),
    "runs": (
        sa.Column("data_region", sa.String(), nullable=True),
        sa.Column("data_classification", sa.String(), nullable=True),
    ),
    "runtime_events": (
        sa.Column("data_region", sa.String(), nullable=True),
        sa.Column("data_classification", sa.String(), nullable=True),
    ),
    "tenant_audit_events": (
        sa.Column("data_region", sa.String(), nullable=True),
        sa.Column("data_classification", sa.String(), nullable=True),
    ),
    "artifacts": (
        sa.Column("storage_region", sa.String(), nullable=True),
        sa.Column("storage_classification", sa.String(), nullable=True),
        sa.Column("storage_class", sa.String(), nullable=True),
    ),
    "evidence_packs": (
        sa.Column("storage_region", sa.String(), nullable=True),
        sa.Column("storage_classification", sa.String(), nullable=True),
        sa.Column("storage_class", sa.String(), nullable=True),
    ),
    "backup_records": (
        sa.Column("storage_region", sa.String(), nullable=True),
        sa.Column("storage_classification", sa.String(), nullable=True),
        sa.Column("storage_class", sa.String(), nullable=True),
    ),
    "executor_registrations": (
        sa.Column("storage_region", sa.String(), nullable=True),
        sa.Column("storage_classification", sa.String(), nullable=True),
        sa.Column("storage_class", sa.String(), nullable=True),
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table_name, columns in TENANT_COLUMNS.items():
        if table_name not in tables:
            continue
        existing = {
            item["name"] for item in sa.inspect(bind).get_columns(table_name)
        }
        for column in columns:
            if column.name not in existing:
                op.add_column(table_name, column.copy())
    tables = set(sa.inspect(bind).get_table_names())
    for table_name in NEW_TABLES:
        if table_name not in tables:
            Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
            tables.add(table_name)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table_name in reversed(NEW_TABLES):
        if table_name in tables:
            op.drop_table(table_name)
    tables = set(sa.inspect(bind).get_table_names())
    for table_name, columns in TENANT_COLUMNS.items():
        if table_name not in tables:
            continue
        existing = {
            item["name"] for item in sa.inspect(bind).get_columns(table_name)
        }
        for column in reversed(columns):
            if column.name in existing:
                with op.batch_alter_table(table_name) as batch:
                    batch.drop_column(str(column.name))
