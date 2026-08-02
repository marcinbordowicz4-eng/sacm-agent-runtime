"""Add task run leases for cross-process orchestrator exclusivity.

Revision ID: e2c6a8f4b1d3
Revises: d4f7a9c2e6b1
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2c6a8f4b1d3"
down_revision: Union[str, Sequence[str], None] = "d4f7a9c2e6b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "task_run_leases" not in tables:
        op.create_table(
            "task_run_leases",
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("owner_token", sa.String(), nullable=False),
            sa.Column("acquired_at", sa.DateTime(), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["task_id"], ["tasks.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("task_id"),
        )
        op.create_index(
            "ix_task_run_leases_owner_token",
            "task_run_leases",
            ["owner_token"],
        )
        op.create_index(
            "ix_task_run_leases_expires_at",
            "task_run_leases",
            ["expires_at"],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "task_run_leases" in tables:
        op.drop_index(
            "ix_task_run_leases_expires_at", table_name="task_run_leases"
        )
        op.drop_index(
            "ix_task_run_leases_owner_token", table_name="task_run_leases"
        )
        op.drop_table("task_run_leases")
