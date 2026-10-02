"""Bind approval decisions to immutable resource digests.

Revision ID: d6e7f8a9b0c1
Revises: c1a2b3c4d5e6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "c1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("approvals")}
    if "resource_digest" not in columns:
        op.add_column("approvals", sa.Column("resource_digest", sa.String(), nullable=True))
        op.create_index("ix_approvals_resource_digest", "approvals", ["resource_digest"])
    if "expires_at" not in columns:
        op.add_column("approvals", sa.Column("expires_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("approvals")}
    if "resource_digest" in columns:
        op.drop_index("ix_approvals_resource_digest", table_name="approvals")
        op.drop_column("approvals", "resource_digest")
    if "expires_at" in columns:
        op.drop_column("approvals", "expires_at")
