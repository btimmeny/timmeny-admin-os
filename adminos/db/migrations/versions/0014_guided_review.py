"""a review whose reading is done elsewhere and whose process is owned here

Revision ID: 0014_guided_review
Revises: 0013_rulebook
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0014_guided_review"
down_revision: str | None = "0013_rulebook"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    """Five tables for a review ChatGPT reads Gmail for and Admin OS holds.

    Nothing existing is touched. `review_runs` and everything hanging off it —
    decisions, prepared scopes, executed actions, verification — keeps working
    exactly as it did, because a review Admin OS built from evidence it holds
    and a review submitted to it are different objects with different
    guarantees, and folding one into the other would weaken both.
    """
    op.create_table(
        "guided_reviews",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("playbook_id", sa.String(length=255), nullable=False),
        sa.Column(
            "playbook_revision_id",
            sa.String(length=36),
            sa.ForeignKey("playbook_revisions.id"),
            nullable=False,
        ),
        sa.Column("current_phase_key", sa.String(length=255), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "supersedes_review_id",
            sa.String(length=36),
            sa.ForeignKey("guided_reviews.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_guided_reviews_date_status", "guided_reviews", ["review_date", "status"]
    )

    op.create_table(
        "guided_review_phases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "review_id",
            sa.String(length=36),
            sa.ForeignKey("guided_reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase_key", sa.String(length=255), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("review_id", "phase_key", name="uq_guided_review_phase_key"),
    )

    op.create_table(
        "guided_review_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "review_id",
            sa.String(length=36),
            sa.ForeignKey("guided_reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "phase_id",
            sa.String(length=36),
            sa.ForeignKey("guided_review_phases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("mailbox_scope", sa.String(length=255), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("thread_count", sa.Integer(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_guided_snapshots_phase", "guided_review_snapshots", ["phase_id", "recorded_at"]
    )

    op.create_table(
        "guided_review_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "review_id",
            sa.String(length=36),
            sa.ForeignKey("guided_reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "phase_id",
            sa.String(length=36),
            sa.ForeignKey("guided_review_phases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(length=36),
            sa.ForeignKey("guided_review_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_thread_id", sa.String(length=255), nullable=False),
        sa.Column("group_key", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("sender", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("why_it_matters", sa.Text(), nullable=True),
        sa.Column("recommended_next_action", sa.Text(), nullable=True),
        sa.Column("recommended_disposition", sa.String(length=255), nullable=True),
        sa.Column("task_required", sa.Boolean(), nullable=True),
        sa.Column("urgency", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("uncertainties", JSON_TYPE, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("snapshot_id", "source_thread_id", name="uq_guided_item_thread"),
    )
    op.create_index(
        "ix_guided_items_group", "guided_review_items", ["snapshot_id", "group_key"]
    )

    op.create_table(
        "guided_review_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "review_id",
            sa.String(length=36),
            sa.ForeignKey("guided_reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("phase_key", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=255), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("detail", JSON_TYPE, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("review_id", "sequence", name="uq_guided_event_sequence"),
    )


def downgrade() -> None:
    op.drop_table("guided_review_events")
    op.drop_index("ix_guided_items_group", table_name="guided_review_items")
    op.drop_table("guided_review_items")
    op.drop_index("ix_guided_snapshots_phase", table_name="guided_review_snapshots")
    op.drop_table("guided_review_snapshots")
    op.drop_table("guided_review_phases")
    op.drop_index("ix_guided_reviews_date_status", table_name="guided_reviews")
    op.drop_table("guided_reviews")
