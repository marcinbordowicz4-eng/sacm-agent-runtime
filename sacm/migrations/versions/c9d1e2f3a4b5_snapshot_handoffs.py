"""Add immutable snapshot handoffs and fenced scope leases.

Revision ID: c9d1e2f3a4b5
Revises: b5e9a3d7f2c4
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "b5e9a3d7f2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "snapshot_handoffs" not in tables:
        op.create_table(
            "snapshot_handoffs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("snapshot_id", sa.String(), nullable=False),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("manifest_hash", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("accepted_by", sa.String(), nullable=True),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(["snapshot_id"], ["run_snapshots.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "snapshot_id", "manifest_hash", name="uq_snapshot_handoff_manifest"
            ),
        ),
        op.create_index(
            op.f("ix_snapshot_handoffs_run_id"), "snapshot_handoffs", ["run_id"]
        )
        op.create_index(
            op.f("ix_snapshot_handoffs_snapshot_id"),
            "snapshot_handoffs",
            ["snapshot_id"],
        )
        op.create_index(
            op.f("ix_snapshot_handoffs_status"), "snapshot_handoffs", ["status"]
        )
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "snapshot_scope_leases" not in tables:
        op.create_table(
            "snapshot_scope_leases",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("scope_key", sa.String(), nullable=False),
            sa.Column("owner", sa.String(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("handoff_id", sa.String(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(["handoff_id"], ["snapshot_handoffs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "scope_key", name="uq_snapshot_scope_lease"),
        )
        op.create_index(
            op.f("ix_snapshot_scope_leases_run_id"),
            "snapshot_scope_leases",
            ["run_id"],
        )
        op.create_index(
            op.f("ix_snapshot_scope_leases_handoff_id"),
            "snapshot_scope_leases",
            ["handoff_id"],
        )
        op.create_index(
            op.f("ix_snapshot_scope_leases_expires_at"),
            "snapshot_scope_leases",
            ["expires_at"],
        )


def downgrade() -> None:
    op.drop_table("snapshot_scope_leases")
    op.drop_table("snapshot_handoffs")
