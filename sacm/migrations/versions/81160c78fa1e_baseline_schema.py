"""Baseline schema.

Revision ID: 81160c78fa1e
"""

from typing import Sequence, Union

from alembic import op

from sacm.infrastructure.db.models import Base

revision: str = "81160c78fa1e"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=connection)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
