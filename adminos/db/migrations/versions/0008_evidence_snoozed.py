"""whether Gmail was holding a thread back, which no label records

Revision ID: 0008_evidence_snoozed
Revises: 0007_review_scope
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0008_evidence_snoozed"
down_revision: str | None = "0007_review_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the column, and leave it null.

    Every existing row was recorded by a search that excluded snoozed mail, so
    backfilling `false` would be defensible — but a review of snoozed mail is
    exactly the place where an assumption that old evidence is awake would show
    up as mail Brian deferred being presented as outstanding. Null says nothing
    was observed, which is the truth, and the next scan of each scope records
    what it sees.
    """
    op.add_column("evidence", sa.Column("snoozed", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("evidence", "snoozed")
