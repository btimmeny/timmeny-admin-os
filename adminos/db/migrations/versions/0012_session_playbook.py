"""the playbook is versioned configuration, and a session is a run of one

Revision ID: 0012_session_playbook
Revises: 0011_review_snapshots
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0012_session_playbook"
down_revision: str | None = "0011_review_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    """Three tables: the playbook's versions, sessions, and their activities.

    Nothing is seeded here. The first revision is written from the file the
    service ships with, the first time a session asks for a playbook, so a
    database migrated before that file changes is not left holding a copy of
    an older one.
    """
    op.create_table(
        "playbook_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("playbook_id", sa.String(length=255), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("document", JSON_TYPE, nullable=False),
        sa.Column("validation", JSON_TYPE, nullable=True),
        sa.Column("change_summary", JSON_TYPE, nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("based_on_revision_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["based_on_revision_id"], ["playbook_revisions.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("playbook_id", "number", name="uq_playbook_revision_number"),
    )
    op.create_index(
        "ix_playbook_revisions_status", "playbook_revisions", ["playbook_id", "status"]
    )

    op.create_table(
        "assistant_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("playbook_id", sa.String(length=255), nullable=False),
        sa.Column("playbook_revision_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("sequence", JSON_TYPE, nullable=False),
        sa.Column("skipped", JSON_TYPE, nullable=False),
        sa.Column("overrides", JSON_TYPE, nullable=True),
        sa.Column("current_activity_key", sa.String(length=255), nullable=True),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("begun_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_session_id", sa.String(length=36), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["playbook_revision_id"], ["playbook_revisions.id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_session_id"], ["assistant_sessions.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_assistant_sessions_status", "assistant_sessions", ["status", "opened_at"]
    )

    op.create_table(
        "session_activities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("activity_key", sa.String(length=255), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("intro", sa.Text(), nullable=True),
        sa.Column("steps", JSON_TYPE, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["assistant_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["review_runs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("session_id", "activity_key", name="uq_session_activity_key"),
    )


def downgrade() -> None:
    """Drops sessions and every version of the playbook they ran.

    Reviews are untouched: a review is the work, and a session is the frame
    around it, so losing the frame does not lose what was decided.
    """
    op.drop_table("session_activities")
    op.drop_index("ix_assistant_sessions_status", table_name="assistant_sessions")
    op.drop_table("assistant_sessions")
    op.drop_index("ix_playbook_revisions_status", table_name="playbook_revisions")
    op.drop_table("playbook_revisions")
