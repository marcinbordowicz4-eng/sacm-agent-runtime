"""Add durable local workflow jobs.

Revision ID: f3a7c9e1b5d2
Revises: e2c6a8f4b1d3
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a7c9e1b5d2"
down_revision: Union[str, Sequence[str], None] = "e2c6a8f4b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "workflow_jobs" in tables:
        return
    op.create_table(
        "workflow_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("lease_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_workflow_jobs_state",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_workflow_jobs_run"),
    )
    for column in ("run_id", "state", "available_at", "lease_token", "lease_expires_at"):
        op.create_index(f"ix_workflow_jobs_{column}", "workflow_jobs", [column])


def downgrade() -> None:
    if "workflow_jobs" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("workflow_jobs")
