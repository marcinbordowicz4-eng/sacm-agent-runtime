"""Add scoped, versioned memory quality metadata.

Revision ID: b5e9a3d7f2c4
Revises: a4d8f2c6e1b3
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5e9a3d7f2c4"
down_revision: Union[str, Sequence[str], None] = "a4d8f2c6e1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    if "memory_chunks" not in set(sa.inspect(connection).get_table_names()):
        return
    columns = {
        column["name"]
        for column in sa.inspect(connection).get_columns("memory_chunks")
    }
    additions = {
        "scope": sa.Column("scope", sa.String(), nullable=True),
        "scope_key": sa.Column("scope_key", sa.String(), nullable=True),
        "content_hash": sa.Column("content_hash", sa.String(), nullable=True),
        "confidence": sa.Column("confidence", sa.Float(), nullable=True),
        "valid_until": sa.Column("valid_until", sa.DateTime(), nullable=True),
        "supersedes_id": sa.Column("supersedes_id", sa.String(), nullable=True),
        "superseded_at": sa.Column("superseded_at", sa.DateTime(), nullable=True),
        "updated_at": sa.Column("updated_at", sa.DateTime(), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("memory_chunks", column)
    rows = connection.execute(
        sa.text("SELECT id, task_id, content, created_at FROM memory_chunks")
    ).mappings()
    for row in rows:
        normalized = " ".join(row["content"].split())
        digest = __import__("hashlib").sha256(normalized.encode()).hexdigest()
        connection.execute(
            sa.text(
                "UPDATE memory_chunks SET scope='task', scope_key=:task_id, "
                "content_hash=:digest, confidence=0.7, updated_at=:created_at "
                "WHERE id=:id"
            ),
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "digest": digest,
                "created_at": row["created_at"],
            },
        )
    with op.batch_alter_table("memory_chunks") as batch:
        batch.alter_column("scope", nullable=False)
        batch.alter_column("scope_key", nullable=False)
        batch.alter_column("content_hash", nullable=False)
        batch.alter_column("confidence", nullable=False)
        batch.alter_column("updated_at", nullable=False)
        batch.create_foreign_key(
            "fk_memory_chunks_supersedes",
            "memory_chunks",
            ["supersedes_id"],
            ["id"],
        )
    existing_indexes = {
        item["name"] for item in sa.inspect(connection).get_indexes("memory_chunks")
    }
    for column in (
        "scope",
        "scope_key",
        "content_hash",
        "valid_until",
        "supersedes_id",
        "superseded_at",
    ):
        index_name = f"ix_memory_chunks_{column}"
        if index_name not in existing_indexes:
            op.create_index(index_name, "memory_chunks", [column])


def downgrade() -> None:
    if "memory_chunks" not in set(
        sa.inspect(op.get_bind()).get_table_names()
    ):
        return
    with op.batch_alter_table("memory_chunks") as batch:
        batch.drop_constraint("fk_memory_chunks_supersedes", type_="foreignkey")
        for column in (
            "updated_at",
            "superseded_at",
            "supersedes_id",
            "valid_until",
            "confidence",
            "content_hash",
            "scope_key",
            "scope",
        ):
            batch.drop_column(column)
