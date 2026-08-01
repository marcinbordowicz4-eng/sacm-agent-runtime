"""Add autonomous failure recovery state.

Revision ID: d4f7a9c2e6b1
Revises: c8d1e4f7a2b5
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f7a9c2e6b1"
down_revision: Union[str, Sequence[str], None] = "c8d1e4f7a2b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "runs" not in tables:
        return
    columns = {column["name"] for column in sa.inspect(connection).get_columns("runs")}
    additions = (
        sa.Column("recovery_state", sa.JSON(), nullable=True),
        sa.Column(
            "recovery_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_failure_classification", sa.String(), nullable=True),
        sa.Column("last_recovery_action", sa.String(), nullable=True),
    )
    for addition in additions:
        if addition.name not in columns:
            op.add_column("runs", addition)
    indexes = {
        index["name"] for index in sa.inspect(connection).get_indexes("runs")
    }
    for column_name in ("last_failure_classification", "last_recovery_action"):
        name = op.f(f"ix_runs_{column_name}")
        if name not in indexes:
            op.create_index(name, "runs", [column_name], unique=False)


def downgrade() -> None:
    connection = op.get_bind()
    if "runs" not in set(sa.inspect(connection).get_table_names()):
        return
    indexes = {
        index["name"] for index in sa.inspect(connection).get_indexes("runs")
    }
    for column_name in ("last_recovery_action", "last_failure_classification"):
        name = op.f(f"ix_runs_{column_name}")
        if name in indexes:
            op.drop_index(name, table_name="runs")
    columns = {column["name"] for column in sa.inspect(connection).get_columns("runs")}
    for column_name in (
        "last_recovery_action",
        "last_failure_classification",
        "recovery_attempt_count",
        "recovery_state",
    ):
        if column_name in columns:
            op.drop_column("runs", column_name)
