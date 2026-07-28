"""action lifecycle, audit, learning events, and candidate rules

Revision ID: 0004_action_lifecycle
Revises: 0003_daily_review
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0004_action_lifecycle"
down_revision: str | None = "0003_daily_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "review_actions",
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
        sa.Column("action_kind", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("params", JSON_TYPE, nullable=True),
        sa.Column("prepared_params", JSON_TYPE, nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("target_thread_id", sa.String(length=255), nullable=True),
        sa.Column("external_kind", sa.String(length=255), nullable=True),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("approval_kind", sa.String(length=255), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=False),
        sa.Column("rule_id", sa.String(length=255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("verification", JSON_TYPE, nullable=True),
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
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_review_action_idempotency"),
    )
    op.create_index("ix_review_actions_run_state", "review_actions", ["run_id", "state"])
    op.create_index("ix_review_actions_item", "review_actions", ["item_id"])

    op.create_table(
        "action_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "action_id",
            sa.String(length=36),
            sa.ForeignKey("review_actions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=255), nullable=False),
        sa.Column("state_after", sa.String(length=255), nullable=False),
        sa.Column("detail", JSON_TYPE, nullable=True),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("action_id", "sequence", name="uq_action_event_sequence"),
    )

    op.create_table(
        "candidate_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("capability_key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("match", JSON_TYPE, nullable=False),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("observed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("policy_version", sa.String(length=255), nullable=True),
        sa.Column("proposed_by", sa.String(length=255), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(length=255), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("automatable_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_reason", sa.Text(), nullable=True),
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
    )
    op.create_index(
        "ix_candidate_rules_capability_state", "candidate_rules", ["capability_key", "state"]
    )

    op.create_table(
        "learning_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("capability_key", sa.String(length=255), nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("review_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "item_id",
            sa.String(length=36),
            sa.ForeignKey("review_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=255), nullable=False),
        sa.Column("recommended", sa.String(length=255), nullable=False),
        sa.Column("chosen", sa.String(length=255), nullable=False),
        sa.Column("recommendation_source", sa.String(length=255), nullable=False),
        sa.Column("policy_version", sa.String(length=255), nullable=False),
        sa.Column("rule_id", sa.String(length=255), nullable=True),
        sa.Column("model_version", sa.String(length=255), nullable=True),
        sa.Column("signals", JSON_TYPE, nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("candidate_rule_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_learning_events_capability", "learning_events", ["capability_key", "kind"]
    )


def downgrade() -> None:
    op.drop_index("ix_learning_events_capability", table_name="learning_events")
    op.drop_table("learning_events")
    op.drop_index("ix_candidate_rules_capability_state", table_name="candidate_rules")
    op.drop_table("candidate_rules")
    op.drop_table("action_events")
    op.drop_index("ix_review_actions_item", table_name="review_actions")
    op.drop_index("ix_review_actions_run_state", table_name="review_actions")
    op.drop_table("review_actions")
