"""baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "operational_objects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("life_area", sa.String(length=255), nullable=True),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["operational_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=255), nullable=False),
        sa.Column("source_thread_id", sa.String(length=255), nullable=False),
        sa.Column("source_message_id", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("participants", JSON_TYPE, nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_system", "source_thread_id", name="uq_evidence_source_thread"),
    )
    op.create_index("ix_evidence_source_thread_id", "evidence", ["source_thread_id"])

    op.create_table(
        "classifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("classifier_version", sa.String(length=255), nullable=False),
        sa.Column("matched_object_id", sa.String(length=36), nullable=True),
        sa.Column("proposed_object_type", sa.String(length=255), nullable=True),
        sa.Column("relationship_type", sa.String(length=255), nullable=False),
        sa.Column("disposition", sa.String(length=255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"]),
        sa.ForeignKeyConstraint(["matched_object_id"], ["operational_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "external_mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("internal_type", sa.String(length=255), nullable=False),
        sa.Column("internal_id", sa.String(length=36), nullable=False),
        sa.Column("external_system", sa.String(length=255), nullable=False),
        sa.Column("external_kind", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("board_id", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("admin_os_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("admin_os_id"),
        sa.UniqueConstraint(
            "external_system", "external_kind", "external_id", name="uq_external_mapping_external"
        ),
        sa.UniqueConstraint(
            "internal_type",
            "internal_id",
            "external_system",
            "external_kind",
            name="uq_external_mapping_internal",
        ),
    )
    op.create_index(
        "ix_external_mappings_internal", "external_mappings", ["internal_type", "internal_id"]
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_name", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=True),
        sa.Column("operational_object_id", sa.String(length=36), nullable=True),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"]),
        sa.ForeignKeyConstraint(["operational_object_id"], ["operational_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_workflow_runs_state_retry", "workflow_runs", ["state", "next_retry_at"])

    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=True),
        sa.Column("response_digest", sa.String(length=64), nullable=True),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "step_name", "sequence", name="uq_workflow_step_identity"),
    )

    op.create_table(
        "decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=True),
        sa.Column("operational_object_id", sa.String(length=36), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", JSON_TYPE, nullable=True),
        sa.Column("selected_option", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["operational_object_id"], ["operational_objects.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("decisions")
    op.drop_table("workflow_steps")
    op.drop_index("ix_workflow_runs_state_retry", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index("ix_external_mappings_internal", table_name="external_mappings")
    op.drop_table("external_mappings")
    op.drop_table("classifications")
    op.drop_index("ix_evidence_source_thread_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_table("operational_objects")
