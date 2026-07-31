"""Add enterprise secret providers and value-free credential leases.

Revision ID: e6a9b4c5d7f1
Revises: d5f8a2b3c4e6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6a9b4c5d7f1"
down_revision: Union[str, Sequence[str], None] = "d5f8a2b3c4e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "executor_registrations" in tables:
        _add_column(
            "executor_registrations",
            sa.Column("public_encryption_key", sa.Text(), nullable=True),
        )
        _add_column(
            "executor_registrations",
            sa.Column("encryption_key_fingerprint", sa.String(), nullable=True),
        )
        _index("executor_registrations", "encryption_key_fingerprint")
    if "execution_jobs" in tables:
        _add_column(
            "execution_jobs",
            sa.Column(
                "secret_requirements",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )

    tables = set(sa.inspect(connection).get_table_names())
    if "secret_provider_configs" not in tables:
        foreign_keys: list[sa.ForeignKeyConstraint] = []
        if "organizations" in tables:
            foreign_keys.append(
                sa.ForeignKeyConstraint(
                    ["organization_id"], ["organizations.id"]
                )
            )
        if "projects" in tables:
            foreign_keys.append(
                sa.ForeignKeyConstraint(["project_id"], ["projects.id"])
            )
        op.create_table(
            "secret_provider_configs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("approved_for_production", sa.Boolean(), nullable=False),
            sa.Column("config_metadata", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("updated_by", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "provider IN ('environment', 'vault', 'aws-secrets-manager', "
                "'aws-sts', 'azure-key-vault', 'azure-managed-identity')",
                name="ck_secret_provider_config_provider",
            ),
            *foreign_keys,
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "organization_id",
                "project_id",
                "name",
                name="uq_secret_provider_config_scope_name",
            ),
        )
        for column in ("organization_id", "project_id", "provider"):
            _index("secret_provider_configs", column)

    tables = set(sa.inspect(connection).get_table_names())
    if "credential_leases" not in tables:
        references = {
            "organization_id": ("organizations", "id"),
            "project_id": ("projects", "id"),
            "task_id": ("tasks", "id"),
            "run_id": ("runs", "id"),
            "job_id": ("execution_jobs", "id"),
            "executor_id": ("executor_registrations", "id"),
            "provider_config_id": ("secret_provider_configs", "id"),
        }
        foreign_keys = [
            sa.ForeignKeyConstraint([column], [f"{table}.{target}"])
            for column, (table, target) in references.items()
            if table in tables
        ]
        op.create_table(
            "credential_leases",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=True),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("executor_id", sa.String(), nullable=False),
            sa.Column("requirement_name", sa.String(), nullable=False),
            sa.Column("requested_permissions", sa.JSON(), nullable=False),
            sa.Column("resource", sa.Text(), nullable=True),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("provider_config_id", sa.String(), nullable=True),
            sa.Column("opaque_handle", sa.String(), nullable=False),
            sa.Column("issued_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("use_count", sa.Integer(), nullable=False),
            sa.Column("audience", sa.String(), nullable=True),
            sa.Column("policy_decision", sa.JSON(), nullable=False),
            sa.Column("provider_lease_id_hash", sa.String(), nullable=True),
            sa.Column("revoked_by", sa.String(), nullable=True),
            sa.Column("revocation_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "use_count >= 0", name="ck_credential_lease_use_count"
            ),
            *foreign_keys,
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "opaque_handle", name="uq_credential_lease_handle"
            ),
        )
        for column in (
            "organization_id",
            "project_id",
            "task_id",
            "run_id",
            "job_id",
            "executor_id",
            "requirement_name",
            "provider",
            "provider_config_id",
            "expires_at",
        ):
            _index("credential_leases", column)


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in ("credential_leases", "secret_provider_configs"):
        if table_name in tables:
            op.drop_table(table_name)
    if "execution_jobs" in tables:
        _drop_column("execution_jobs", "secret_requirements")
    if "executor_registrations" in tables:
        _drop_column("executor_registrations", "encryption_key_fingerprint")
        _drop_column("executor_registrations", "public_encryption_key")


def _add_column(table_name: str, column: sa.Column) -> None:
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    if column.name not in columns:
        op.add_column(table_name, column)


def _drop_column(table_name: str, column_name: str) -> None:
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
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
