"""where a review looked, recorded on the run and on the evidence

Revision ID: 0007_review_scope
Revises: 0006_action_scopes
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0007_review_scope"
down_revision: str | None = "0006_action_scopes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

INBOX_SCOPE = {
    "name": "inbox",
    "mailbox": "INBOX",
    "include_snoozed": False,
    "include_archived": False,
    "include_trash": False,
    "include_spam": False,
    "include_sent": False,
    "include_drafts": False,
    "requested": False,
    "gmail_query": "-in:snoozed",
    "description": (
        "Mail in the inbox now: archived, snoozed, trashed, spam, sent-only "
        "and draft-only threads were excluded."
    ),
}
"""What every run so far was, whether or not it said so."""

REVIEW_RUNS = sa.table(
    "review_runs",
    sa.column("review_date", sa.Date),
    sa.column("channel", sa.String),
    sa.column("scope_name", sa.String),
    sa.column("scope", JSON_TYPE),
)


def upgrade() -> None:
    op.add_column("evidence", sa.Column("label_ids", JSON_TYPE, nullable=True))
    op.add_column("review_runs", sa.Column("scope_name", sa.String(length=255), nullable=True))
    op.add_column("review_runs", sa.Column("scope", JSON_TYPE, nullable=True))

    op.execute(REVIEW_RUNS.update().values(scope_name="inbox", scope=INBOX_SCOPE))

    with op.batch_alter_table("review_runs") as batch:
        batch.alter_column("scope_name", existing_type=sa.String(length=255), nullable=False)
        batch.alter_column("scope", existing_type=JSON_TYPE, nullable=False)
        batch.drop_constraint("uq_review_run_date_channel", type_="unique")
        batch.create_unique_constraint(
            "uq_review_run_date_channel_scope",
            ["review_date", "channel", "scope_name"],
        )


def downgrade() -> None:
    """Refuses rather than deletes if a review of another scope exists.

    The old constraint cannot hold two scopes of the same day, and losing a
    review to a schema change would be the wrong way to find that out.
    """
    clashes = (
        op.get_bind()
        .execute(
            sa.select(REVIEW_RUNS.c.review_date, REVIEW_RUNS.c.channel)
            .group_by(REVIEW_RUNS.c.review_date, REVIEW_RUNS.c.channel)
            .having(sa.func.count() > 1)
        )
        .all()
    )
    if clashes:
        raise RuntimeError(
            "Reviews of more than one scope exist on "
            + ", ".join(f"{day} ({channel})" for day, channel in clashes)
            + ". Retire the extra runs before going back to one review a day."
        )

    with op.batch_alter_table("review_runs") as batch:
        batch.drop_constraint("uq_review_run_date_channel_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_review_run_date_channel",
            ["review_date", "channel"],
        )
        batch.drop_column("scope")
        batch.drop_column("scope_name")

    op.drop_column("evidence", "label_ids")
