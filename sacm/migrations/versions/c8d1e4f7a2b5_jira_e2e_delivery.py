"""Add durable Jira Cloud delivery records.

Revision ID: c8d1e4f7a2b5
Revises: b2e5f8a1c3d7
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8d1e4f7a2b5"
down_revision: Union[str, Sequence[str], None] = "b2e5f8a1c3d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if "jira_connectors" not in tables:
        op.create_table(
            "jira_connectors",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("base_url", sa.String(), nullable=False),
            sa.Column("jira_project_key", sa.String(), nullable=False),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("api_token_ref", sa.String(), nullable=False),
            sa.Column("webhook_secret_ref", sa.String(), nullable=True),
            sa.Column("field_mapping", sa.JSON(), nullable=False),
            sa.Column("status_mapping", sa.JSON(), nullable=False),
            sa.Column("timeout_seconds", sa.Float(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "organization_id",
                "jira_project_key",
                name="uq_jira_connectors_organization_project",
            ),
        )
        op.create_index(
            op.f("ix_jira_connectors_organization_id"),
            "jira_connectors",
            ["organization_id"],
        )
        op.create_index(
            op.f("ix_jira_connectors_project_id"),
            "jira_connectors",
            ["project_id"],
        )
        op.create_index(
            op.f("ix_jira_connectors_jira_project_key"),
            "jira_connectors",
            ["jira_project_key"],
        )

    tables = set(sa.inspect(connection).get_table_names())
    if "jira_webhook_deliveries" not in tables:
        op.create_table(
            "jira_webhook_deliveries",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("connector_id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("delivery_id", sa.String(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("payload_hash", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("received_at", sa.DateTime(), nullable=False),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["connector_id"], ["jira_connectors.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "connector_id",
                "delivery_id",
                name="uq_jira_webhook_delivery",
            ),
        )
        for column in ("connector_id", "organization_id", "project_id", "task_id"):
            op.create_index(
                op.f(f"ix_jira_webhook_deliveries_{column}"),
                "jira_webhook_deliveries",
                [column],
            )

    tables = set(sa.inspect(connection).get_table_names())
    if "jira_connector_operations" not in tables:
        op.create_table(
            "jira_connector_operations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("connector_id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("operation_type", sa.String(), nullable=False),
            sa.Column("idempotency_key", sa.String(), nullable=False),
            sa.Column("payload_hash", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("external_id", sa.String(), nullable=True),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["connector_id"], ["jira_connectors.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "connector_id",
                "idempotency_key",
                name="uq_jira_connector_operation",
            ),
        )
        for column in (
            "connector_id",
            "organization_id",
            "project_id",
            "task_id",
            "run_id",
            "operation_type",
        ):
            op.create_index(
                op.f(f"ix_jira_connector_operations_{column}"),
                "jira_connector_operations",
                [column],
            )

    tables = set(sa.inspect(connection).get_table_names())
    if "jira_delivery_states" not in tables:
        op.create_table(
            "jira_delivery_states",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("connector_id", sa.String(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=False),
            sa.Column("project_id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("jira_status", sa.String(), nullable=True),
            sa.Column("status_comment_id", sa.String(), nullable=True),
            sa.Column("pr_status", sa.String(), nullable=False),
            sa.Column("pr_url", sa.String(), nullable=True),
            sa.Column("context", sa.JSON(), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["connector_id"], ["jira_connectors.id"]),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("task_id", name="uq_jira_delivery_states_task"),
        )
        for column in (
            "connector_id",
            "organization_id",
            "project_id",
            "task_id",
            "run_id",
            "status",
        ):
            op.create_index(
                op.f(f"ix_jira_delivery_states_{column}"),
                "jira_delivery_states",
                [column],
            )


def downgrade() -> None:
    for table in (
        "jira_delivery_states",
        "jira_connector_operations",
        "jira_webhook_deliveries",
        "jira_connectors",
    ):
        if table in set(sa.inspect(op.get_bind()).get_table_names()):
            op.drop_table(table)
