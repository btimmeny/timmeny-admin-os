"""a review states its plan before it is worked

Revision ID: 0010_review_plans
Revises: 0009_review_revisions
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0010_review_plans"
down_revision: str | None = "0009_review_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    """Give every review the plan it will be worked in.

    Existing reviews get no plan row: one is written the next time they are
    read, as `active`, because a review already under way was begun by
    somebody and asking for it to be begun again would be a fiction.
    """
    op.create_table(
        "review_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sequence", JSON_TYPE, nullable=False),
        sa.Column("skipped", JSON_TYPE, nullable=False),
        sa.Column("config_version", sa.String(length=255), nullable=False),
        sa.Column("begun_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("begun_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["review_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_review_plan_run"),
    )


def downgrade() -> None:
    op.drop_table("review_plans")
