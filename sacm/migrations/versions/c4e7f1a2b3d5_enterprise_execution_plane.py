"""Add enterprise executor registrations and execution jobs.

Revision ID: c4e7f1a2b3d5
Revises: a9c0d1e2f3b4
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e7f1a2b3d5"
down_revision: Union[str, Sequence[str], None] = "a9c0d1e2f3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "executor_enrollment_tokens" not in tables:
        op.create_table(
            "executor_enrollment_tokens",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=True),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("customer_deployment_id", sa.String(), nullable=True),
            sa.Column("scope_key", sa.String(), nullable=False),
            sa.Column("token_hash", sa.String(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "(organization_id IS NOT NULL AND project_id IS NULL "
                "AND customer_deployment_id IS NULL) OR "
                "(organization_id IS NULL AND project_id IS NOT NULL "
                "AND customer_deployment_id IS NULL) OR "
                "(organization_id IS NULL AND project_id IS NULL "
                "AND customer_deployment_id IS NOT NULL)",
                name="ck_executor_enrollment_token_scope",
            ),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "token_hash", name="uq_executor_enrollment_token_hash"
            ),
        )
        for column in (
            "organization_id",
            "project_id",
            "customer_deployment_id",
            "scope_key",
        ):
            op.create_index(
                op.f(f"ix_executor_enrollment_tokens_{column}"),
                "executor_enrollment_tokens",
                [column],
                unique=False,
            )

    tables = set(sa.inspect(connection).get_table_names())
    if "executor_registrations" not in tables:
        op.create_table(
            "executor_registrations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=True),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("customer_deployment_id", sa.String(), nullable=True),
            sa.Column("scope_key", sa.String(), nullable=False),
            sa.Column("executor_identity", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("capabilities", sa.JSON(), nullable=False),
            sa.Column("labels", sa.JSON(), nullable=False),
            sa.Column("runtime_kind", sa.String(), nullable=False),
            sa.Column("sandbox_runtime", sa.String(), nullable=False),
            sa.Column("sandbox_policy", sa.JSON(), nullable=False),
            sa.Column("public_signing_key", sa.Text(), nullable=False),
            sa.Column("signing_key_fingerprint", sa.String(), nullable=False),
            sa.Column("auth_token_hash", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("version", sa.String(), nullable=False),
            sa.Column("network_boundary", sa.JSON(), nullable=False),
            sa.Column("enrolled_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_by", sa.String(), nullable=True),
            sa.Column("revocation_reason", sa.Text(), nullable=True),
            sa.CheckConstraint(
                "(organization_id IS NOT NULL AND project_id IS NULL "
                "AND customer_deployment_id IS NULL) OR "
                "(organization_id IS NULL AND project_id IS NOT NULL "
                "AND customer_deployment_id IS NULL) OR "
                "(organization_id IS NULL AND project_id IS NULL "
                "AND customer_deployment_id IS NOT NULL)",
                name="ck_executor_registration_scope",
            ),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "auth_token_hash", name="uq_executor_auth_token_hash"
            ),
            sa.UniqueConstraint(
                "scope_key",
                "executor_identity",
                name="uq_executor_scope_identity",
            ),
        )
        for column in (
            "organization_id",
            "project_id",
            "customer_deployment_id",
            "scope_key",
            "signing_key_fingerprint",
            "status",
        ):
            op.create_index(
                op.f(f"ix_executor_registrations_{column}"),
                "executor_registrations",
                [column],
                unique=False,
            )

    tables = set(sa.inspect(connection).get_table_names())
    if "execution_jobs" not in tables:
        op.create_table(
            "execution_jobs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=True),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("customer_deployment_id", sa.String(), nullable=True),
            sa.Column("scope_key", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("run_step_id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("idempotency_key", sa.String(), nullable=False),
            sa.Column("required_capabilities", sa.JSON(), nullable=False),
            sa.Column("required_labels", sa.JSON(), nullable=False),
            sa.Column("payload_contract", sa.JSON(), nullable=False),
            sa.Column("payload_hash", sa.String(), nullable=False),
            sa.Column("payload_signature", sa.Text(), nullable=False),
            sa.Column("payload_signature_metadata", sa.JSON(), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("lease_owner_id", sa.String(), nullable=True),
            sa.Column("lease_token_hash", sa.String(), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("lease_heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("result_contract", sa.JSON(), nullable=True),
            sa.Column("result_hash", sa.String(), nullable=True),
            sa.Column("result_signature", sa.Text(), nullable=True),
            sa.Column("result_signature_metadata", sa.JSON(), nullable=True),
            sa.Column("failure", sa.JSON(), nullable=True),
            sa.Column("queued_at", sa.DateTime(), nullable=False),
            sa.Column("leased_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("failed_at", sa.DateTime(), nullable=True),
            sa.Column("expired_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "state IN ('QUEUED', 'LEASED', 'RUNNING', 'COMPLETED', "
                "'FAILED', 'EXPIRED', 'CANCELLED')",
                name="ck_execution_job_state",
            ),
            sa.ForeignKeyConstraint(
                ["lease_owner_id"], ["executor_registrations.id"]
            ),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.ForeignKeyConstraint(["run_step_id"], ["run_steps.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "scope_key",
                "idempotency_key",
                name="uq_execution_job_idempotency",
            ),
        )
        for column in (
            "organization_id",
            "project_id",
            "customer_deployment_id",
            "scope_key",
            "run_id",
            "run_step_id",
            "task_id",
            "state",
            "lease_owner_id",
            "lease_expires_at",
        ):
            op.create_index(
                op.f(f"ix_execution_jobs_{column}"),
                "execution_jobs",
                [column],
                unique=False,
            )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in (
        "execution_jobs",
        "executor_registrations",
        "executor_enrollment_tokens",
    ):
        if table_name in tables:
            op.drop_table(table_name)
