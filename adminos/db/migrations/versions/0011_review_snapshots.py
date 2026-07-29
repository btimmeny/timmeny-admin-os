"""a review is a snapshot of the mailbox, and says which one it replaced

Revision ID: 0011_review_snapshots
Revises: 0010_review_plans
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0011_review_snapshots"
down_revision: str | None = "0010_review_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record when a review's mailbox was read, and whose place it took.

    `snapshot_at` differs from `evidence_refresh_at` in not moving: one says
    the mailbox this review is of, the other when it was last topped up.

    Both stay null on existing rows. Reviews made before this change were
    snapshots too, but nobody recorded when, and a backfilled timestamp reads
    exactly like a real one.
    """
    op.add_column(
        "review_runs", sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "review_runs",
        sa.Column("supersedes_run_id", sa.String(length=36), nullable=True),
    )
    with op.batch_alter_table("review_runs") as batch:
        batch.create_foreign_key(
            "fk_review_run_supersedes",
            "review_runs",
            ["supersedes_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """Loses which review replaced which, and keeps every review."""
    with op.batch_alter_table("review_runs") as batch:
        batch.drop_constraint("fk_review_run_supersedes", type_="foreignkey")
        batch.drop_column("supersedes_run_id")
        batch.drop_column("snapshot_at")
