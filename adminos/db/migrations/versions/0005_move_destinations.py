"""destinations carried by a recommendation and by a learned rule

Revision ID: 0005_move_destinations
Revises: 0004_action_lifecycle
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0005_move_destinations"
down_revision: str | None = "0004_action_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("review_items", sa.Column("recommendation_params", JSON_TYPE, nullable=True))
    op.add_column("candidate_rules", sa.Column("action_params", JSON_TYPE, nullable=True))


def downgrade() -> None:
    op.drop_column("candidate_rules", "action_params")
    op.drop_column("review_items", "recommendation_params")
