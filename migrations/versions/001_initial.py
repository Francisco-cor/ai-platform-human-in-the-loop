"""initial — Fase 1

Revision ID: 001_initial
Revises:
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_executions",
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_node", sa.String(length=64), nullable=True),
        sa.Column("normalized_request", sa.JSON(), nullable=True),
        sa.Column("proposal", sa.JSON(), nullable=True),
        sa.Column("approval_request", sa.JSON(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("execution_id"),
    )
    op.create_index(op.f("ix_workflow_executions_request_id"), "workflow_executions", ["request_id"], unique=False)
    op.create_index(op.f("ix_workflow_executions_status"), "workflow_executions", ["status"], unique=False)
    op.create_index(op.f("ix_workflow_executions_tenant_id"), "workflow_executions", ["tenant_id"], unique=False)
    op.create_index("ix_executions_tenant_status", "workflow_executions", ["tenant_id", "status"], unique=False)

    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("input_hash", sa.String(length=128), nullable=True),
        sa.Column("output_hash", sa.String(length=128), nullable=True),
        sa.Column("policy_decisions", sa.JSON(), nullable=True),
        sa.Column("model_metadata", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["execution_id"], ["workflow_executions.execution_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_audit_events_execution_id"), "audit_events", ["execution_id"], unique=False)

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "workflow_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
        sa.Column("execution_id", sa.String(length=64), nullable=False),
        sa.Column("node", sa.String(length=64), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["workflow_executions.execution_id"]),
        sa.PrimaryKeyConstraint("checkpoint_id"),
    )
    op.create_index(op.f("ix_workflow_checkpoints_execution_id"), "workflow_checkpoints", ["execution_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_workflow_checkpoints_execution_id"), table_name="workflow_checkpoints")
    op.drop_table("workflow_checkpoints")
    op.drop_table("idempotency_keys")
    op.drop_index(op.f("ix_audit_events_execution_id"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_executions_tenant_status", table_name="workflow_executions")
    op.drop_index(op.f("ix_workflow_executions_tenant_id"), table_name="workflow_executions")
    op.drop_index(op.f("ix_workflow_executions_status"), table_name="workflow_executions")
    op.drop_index(op.f("ix_workflow_executions_request_id"), table_name="workflow_executions")
    op.drop_table("workflow_executions")
