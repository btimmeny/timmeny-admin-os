"""a review can be abandoned and the same day reviewed again

Revision ID: 0009_review_revisions
Revises: 0008_evidence_snoozed
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0009_review_revisions"
down_revision: str | None = "0008_evidence_snoozed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REVIEW_RUNS = sa.table(
    "review_runs",
    sa.column("review_date", sa.Date),
    sa.column("channel", sa.String),
    sa.column("scope_name", sa.String),
    sa.column("revision", sa.Integer),
)


def upgrade() -> None:
    """Give a review a revision, and room to say when it was last read.

    Every existing run is revision 1 of its day and scope, which is what the
    old uniqueness meant. Widening the constraint to include the revision is
    what lets a finished day be started again without pretending the first
    review never happened.

    `evidence_refresh_at` stays null on old rows: they were refreshed, but
    nobody recorded when, and a made-up timestamp reads exactly like a real one.
    """
    op.add_column(
        "review_runs",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "review_runs", sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "review_runs", sa.Column("evidence_refresh_at", sa.DateTime(timezone=True), nullable=True)
    )

    with op.batch_alter_table("review_runs") as batch:
        batch.drop_constraint("uq_review_run_date_channel_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_review_run_date_channel_scope_revision",
            ["review_date", "channel", "scope_name", "revision"],
        )


def downgrade() -> None:
    """Refuses rather than deletes where a day was reviewed twice.

    The old constraint cannot hold two revisions of the same day and scope,
    and a review Brian worked through is not something to lose to a schema
    change.
    """
    clashes = (
        op.get_bind()
        .execute(
            sa.select(REVIEW_RUNS.c.review_date, REVIEW_RUNS.c.scope_name)
            .group_by(REVIEW_RUNS.c.review_date, REVIEW_RUNS.c.channel, REVIEW_RUNS.c.scope_name)
            .having(sa.func.count() > 1)
        )
        .all()
    )
    if clashes:
        raise RuntimeError(
            "More than one revision of a review exists on "
            + ", ".join(f"{day} ({scope_name})" for day, scope_name in clashes)
            + ". Retire the extra revisions before going back to one review a day."
        )

    with op.batch_alter_table("review_runs") as batch:
        batch.drop_constraint("uq_review_run_date_channel_scope_revision", type_="unique")
        batch.create_unique_constraint(
            "uq_review_run_date_channel_scope",
            ["review_date", "channel", "scope_name"],
        )
        batch.drop_column("evidence_refresh_at")
        batch.drop_column("abandoned_at")
        batch.drop_column("revision")
