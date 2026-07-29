"""the exact selection an execution is allowed to touch

Revision ID: 0006_action_scopes
Revises: 0005_move_destinations
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0006_action_scopes"
down_revision: str | None = "0005_move_destinations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "action_scopes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("capability_key", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=255), nullable=False),
        sa.Column("entire_capability", sa.Boolean(), nullable=False),
        sa.Column("requested_item_ids", JSON_TYPE, nullable=False),
        sa.Column("prepared_item_ids", JSON_TYPE, nullable=False),
        sa.Column("action_ids", JSON_TYPE, nullable=False),
        sa.Column("excluded", JSON_TYPE, nullable=False),
        sa.Column("executed_item_ids", JSON_TYPE, nullable=True),
        sa.Column("verified_item_ids", JSON_TYPE, nullable=True),
        sa.Column("superseded_by", sa.String(length=36), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["review_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_action_scopes_run",
        "action_scopes",
        ["run_id", "capability_key", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_action_scopes_run", table_name="action_scopes")
    op.drop_table("action_scopes")
