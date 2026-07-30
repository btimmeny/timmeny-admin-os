"""a rule is a record with versions, and every move it made is kept

Revision ID: 0013_rulebook
Revises: 0012_session_playbook
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0013_rulebook"
down_revision: str | None = "0012_session_playbook"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    """Three tables: a rule, its versions, and what happened to it.

    Nothing is seeded and nothing is migrated into them. The learning path's
    `candidate_rules` stays exactly where it is and keeps working; folding it
    in is a data migration of its own, and doing it here would mean rewriting
    rules that are currently recommending.
    """
    op.create_table(
        "rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("rule_type", sa.String(length=255), nullable=False),
        sa.Column("capability_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("current_version_id", sa.String(length=36), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rules_type_status", "rules", ["rule_type", "status"])
    op.create_index("ix_rules_capability_status", "rules", ["capability_key", "status"])

    op.create_table(
        "rule_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("match", JSON_TYPE, nullable=False),
        sa.Column("effects", JSON_TYPE, nullable=False),
        sa.Column("constraints", JSON_TYPE, nullable=False),
        sa.Column("examples", JSON_TYPE, nullable=True),
        sa.Column("summary", JSON_TYPE, nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("supersedes_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"], ["rule_versions.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("rule_id", "number", name="uq_rule_version_number"),
    )

    op.create_table(
        "rule_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=255), nullable=False),
        sa.Column("from_status", sa.String(length=255), nullable=True),
        sa.Column("to_status", sa.String(length=255), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("detail", JSON_TYPE, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["rule_versions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_rule_events_rule", "rule_events", ["rule_id", "created_at"])


def downgrade() -> None:
    """Drops the rulebook. Reviews and learned candidate rules are untouched."""
    op.drop_index("ix_rule_events_rule", table_name="rule_events")
    op.drop_table("rule_events")
    op.drop_table("rule_versions")
    op.drop_index("ix_rules_capability_status", table_name="rules")
    op.drop_index("ix_rules_type_status", table_name="rules")
    op.drop_table("rules")
