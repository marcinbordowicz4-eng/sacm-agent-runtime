"""Add durable lifecycle metrics.

Revision ID: a4d8f2c6e1b3
Revises: f3a7c9e1b5d2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4d8f2c6e1b3"
down_revision: Union[str, Sequence[str], None] = "f3a7c9e1b5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "lifecycle_metrics" in tables:
        return
    op.create_table(
        "lifecycle_metrics",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("task_id", "run_id", "metric", "created_at"):
        op.create_index(
            f"ix_lifecycle_metrics_{column}", "lifecycle_metrics", [column]
        )


def downgrade() -> None:
    if "lifecycle_metrics" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("lifecycle_metrics")
