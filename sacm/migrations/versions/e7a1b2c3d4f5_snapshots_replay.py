"""Add durable run snapshots and replay links.

Revision ID: e7a1b2c3d4f5
Revises: d2f6c8a1e4b7
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1b2c3d4f5"
down_revision: Union[str, Sequence[str], None] = "d2f6c8a1e4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "run_snapshots" not in tables:
        op.create_table(
            "run_snapshots",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("event_sequence", sa.Integer(), nullable=False),
            sa.Column("event_hash", sa.String(), nullable=False),
            sa.Column("workflow_version", sa.String(), nullable=False),
            sa.Column("run_state", sa.JSON(), nullable=False),
            sa.Column("step_state", sa.JSON(), nullable=False),
            sa.Column("execution_plan_summary", sa.JSON(), nullable=True),
            sa.Column("context_summary", sa.JSON(), nullable=False),
            sa.Column("checksum", sa.String(), nullable=False),
            sa.Column("parent_snapshot_id", sa.String(), nullable=True),
            sa.Column("creation_reason", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["parent_snapshot_id"], ["run_snapshots.id"]
            ),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "run_id",
                "checksum",
                name="uq_run_snapshots_run_checksum",
            ),
        )
        op.create_index(
            op.f("ix_run_snapshots_run_id"),
            "run_snapshots",
            ["run_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_run_snapshots_task_id"),
            "run_snapshots",
            ["task_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_run_snapshots_parent_snapshot_id"),
            "run_snapshots",
            ["parent_snapshot_id"],
            unique=False,
        )

    tables = set(sa.inspect(connection).get_table_names())
    if "run_replays" not in tables:
        op.create_table(
            "run_replays",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=False),
            sa.Column("source_run_id", sa.String(), nullable=False),
            sa.Column("source_snapshot_id", sa.String(), nullable=False),
            sa.Column("replay_run_id", sa.String(), nullable=False),
            sa.Column("overrides", sa.JSON(), nullable=False),
            sa.Column("replay_reason", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["replay_run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(["source_run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(
                ["source_snapshot_id"], ["run_snapshots.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("replay_run_id"),
        )
        op.create_index(
            op.f("ix_run_replays_source_run_id"),
            "run_replays",
            ["source_run_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_run_replays_source_snapshot_id"),
            "run_replays",
            ["source_snapshot_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_run_replays_replay_run_id"),
            "run_replays",
            ["replay_run_id"],
            unique=True,
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "run_replays" in tables:
        op.drop_table("run_replays")
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "run_snapshots" in tables:
        op.drop_table("run_snapshots")
