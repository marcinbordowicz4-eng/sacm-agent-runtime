"""Add enterprise software supply-chain evidence and attestations.

Revision ID: f7b0c5d6e8a2
Revises: e6a9b4c5d7f1
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7b0c5d6e8a2"
down_revision: Union[str, Sequence[str], None] = "e6a9b4c5d7f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "runs" in tables:
        _add_column(
            "runs",
            sa.Column(
                "supply_chain_status",
                sa.String(),
                nullable=False,
                server_default="NOT_EVALUATED",
            ),
        )
        _add_column(
            "runs",
            sa.Column(
                "missing_supply_chain_evidence",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
    if "evidence_packs" in tables:
        for column in (
            sa.Column("pack_hash", sa.String(), nullable=True),
            sa.Column("previous_pack_hash", sa.String(), nullable=True),
            sa.Column("signature_algorithm", sa.String(), nullable=True),
            sa.Column("signature_key_id", sa.String(), nullable=True),
            sa.Column("public_key_fingerprint", sa.String(), nullable=True),
            sa.Column("public_key", sa.Text(), nullable=True),
            sa.Column("signature", sa.Text(), nullable=True),
            sa.Column(
                "verification_status",
                sa.String(),
                nullable=False,
                server_default="UNVERIFIED",
            ),
        ):
            _add_column("evidence_packs", column)

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _create_images(tables)
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _create_releases(tables)
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _create_records(tables)
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _create_attestations(tables)


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in (
        "supply_chain_attestations",
        "supply_chain_records",
        "supply_chain_releases",
        "supply_chain_images",
    ):
        if table_name in tables:
            op.drop_table(table_name)
    if "evidence_packs" in tables:
        for name in (
            "verification_status",
            "signature",
            "public_key",
            "public_key_fingerprint",
            "signature_key_id",
            "signature_algorithm",
            "previous_pack_hash",
            "pack_hash",
        ):
            _drop_column("evidence_packs", name)
    if "runs" in tables:
        _drop_column("runs", "missing_supply_chain_evidence")
        _drop_column("runs", "supply_chain_status")


def _create_images(tables: set[str]) -> None:
    if "supply_chain_images" in tables:
        return
    constraints = _foreign_keys(tables, {"run_id": ("runs", "id")})
    op.create_table(
        "supply_chain_images",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("repository", sa.String(), nullable=True),
        sa.Column("tag", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *constraints,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "name", "digest", name="uq_supply_chain_images_identity"
        ),
    )
    _index("supply_chain_images", "run_id")


def _create_releases(tables: set[str]) -> None:
    if "supply_chain_releases" in tables:
        return
    constraints = _foreign_keys(tables, {"run_id": ("runs", "id")})
    op.create_table(
        "supply_chain_releases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *constraints,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "name", "version", name="uq_supply_chain_releases_identity"
        ),
    )
    _index("supply_chain_releases", "run_id")


def _create_records(tables: set[str]) -> None:
    if "supply_chain_records" in tables:
        return
    references = {
        "run_id": ("runs", "id"),
        "evidence_pack_id": ("evidence_packs", "id"),
        "artifact_id": ("artifacts", "id"),
        "image_id": ("supply_chain_images", "id"),
        "release_id": ("supply_chain_releases", "id"),
    }
    op.create_table(
        "supply_chain_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("evidence_pack_id", sa.String(), nullable=True),
        sa.Column("artifact_id", sa.String(), nullable=True),
        sa.Column("image_id", sa.String(), nullable=True),
        sa.Column("release_id", sa.String(), nullable=True),
        sa.Column("record_type", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("subject_name", sa.String(), nullable=False),
        sa.Column("subject_digest", sa.String(), nullable=False),
        sa.Column("artifact_sha256", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "record_type IN ('sbom', 'provenance', 'dependency_scan', "
            "'secret_scan', 'iac_scan', 'container_scan')",
            name="ck_supply_chain_record_type",
        ),
        *_foreign_keys(tables, references),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "run_id",
        "evidence_pack_id",
        "artifact_id",
        "image_id",
        "release_id",
        "record_type",
    ):
        _index("supply_chain_records", column)


def _create_attestations(tables: set[str]) -> None:
    if "supply_chain_attestations" in tables:
        return
    references = {
        "run_id": ("runs", "id"),
        "record_id": ("supply_chain_records", "id"),
        "artifact_id": ("artifacts", "id"),
        "image_id": ("supply_chain_images", "id"),
        "release_id": ("supply_chain_releases", "id"),
    }
    op.create_table(
        "supply_chain_attestations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("record_id", sa.String(), nullable=True),
        sa.Column("artifact_id", sa.String(), nullable=True),
        sa.Column("image_id", sa.String(), nullable=True),
        sa.Column("release_id", sa.String(), nullable=True),
        sa.Column("subject_name", sa.String(), nullable=False),
        sa.Column("subject_digest", sa.String(), nullable=False),
        sa.Column("predicate_type", sa.String(), nullable=False),
        sa.Column("statement", sa.JSON(), nullable=False),
        sa.Column("statement_hash", sa.String(), nullable=False),
        sa.Column("previous_attestation_hash", sa.String(), nullable=True),
        sa.Column("attestation_hash", sa.String(), nullable=False),
        sa.Column("signature_algorithm", sa.String(), nullable=False),
        sa.Column("signature_key_id", sa.String(), nullable=True),
        sa.Column("public_key_fingerprint", sa.String(), nullable=True),
        sa.Column("public_key", sa.Text(), nullable=True),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_foreign_keys(tables, references),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "run_id",
        "record_id",
        "artifact_id",
        "image_id",
        "release_id",
    ):
        _index("supply_chain_attestations", column)


def _foreign_keys(
    tables: set[str], references: dict[str, tuple[str, str]]
) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint([column], [f"{table}.{target}"])
        for column, (table, target) in references.items()
        if table in tables
    ]


def _add_column(table_name: str, column: sa.Column) -> None:
    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    if column.name not in columns:
        op.add_column(table_name, column)


def _drop_column(table_name: str, column_name: str) -> None:
    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    if column_name in columns:
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column(column_name)


def _index(table_name: str, column_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes(table_name)}
    name = op.f(f"ix_{table_name}_{column_name}")
    if name not in indexes:
        op.create_index(name, table_name, [column_name], unique=False)
