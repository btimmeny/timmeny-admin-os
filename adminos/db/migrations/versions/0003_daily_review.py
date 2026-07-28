"""daily review runs, groups, items, and decisions

Revision ID: 0003_daily_review
Revises: 0002_classification_identity
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003_daily_review"
down_revision: str | None = "0002_classification_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("evidence", sa.Column("capability_keys", JSON_TYPE, nullable=True))

    op.create_table(
        "review_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("channel", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("config_version", sa.String(length=255), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("review_date", "channel", name="uq_review_run_date_channel"),
    )

    op.create_table(
        "review_groups",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("review_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability_key", sa.String(length=255), nullable=False),
        sa.Column("capability_name", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("run_id", "capability_key", name="uq_review_group_capability"),
    )

    op.create_table(
        "review_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("review_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.String(length=36),
            sa.ForeignKey("review_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("source_thread_id", sa.String(length=255), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("participants", JSON_TYPE, nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("recommendation", sa.String(length=255), nullable=False),
        sa.Column("recommendation_source", sa.String(length=255), nullable=False),
        sa.Column("recommendation_confidence", sa.Float(), nullable=False),
        sa.Column("recommendation_rationale", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.String(length=255), nullable=False),
        sa.Column("rule_id", sa.String(length=255), nullable=True),
        sa.Column("model_version", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("objective_keys", JSON_TYPE, nullable=True),
        sa.Column("provenance", JSON_TYPE, nullable=True),
        sa.Column("approved_action", sa.String(length=255), nullable=True),
        sa.Column("approved_params", JSON_TYPE, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("group_id", "evidence_id", name="uq_review_item_evidence"),
    )
    op.create_index("ix_review_items_run_state", "review_items", ["run_id", "state"])

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("review_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.String(length=36),
            sa.ForeignKey("review_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability_key", sa.String(length=255), nullable=False),
        sa.Column("decision", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=255), nullable=True),
        sa.Column("action_params", JSON_TYPE, nullable=True),
        sa.Column("followed_recommendation", sa.Boolean(), nullable=False),
        sa.Column("recommendation", sa.String(length=255), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("learning_scope", sa.String(length=255), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_review_decisions_item", "review_decisions", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_review_decisions_item", table_name="review_decisions")
    op.drop_table("review_decisions")
    op.drop_index("ix_review_items_run_state", table_name="review_items")
    op.drop_table("review_items")
    op.drop_table("review_groups")
    op.drop_table("review_runs")
    with op.batch_alter_table("evidence") as batch:
        batch.drop_column("capability_keys")
