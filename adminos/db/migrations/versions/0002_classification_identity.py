"""classification identity

Revision ID: 0002_classification_identity
Revises: 0001_baseline
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0002_classification_identity"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "uq_classification_evidence_version"


def upgrade() -> None:
    # Batch mode: SQLite cannot ALTER a constraint into an existing table.
    with op.batch_alter_table("classifications") as batch:
        batch.create_unique_constraint(CONSTRAINT_NAME, ["evidence_id", "classifier_version"])


def downgrade() -> None:
    with op.batch_alter_table("classifications") as batch:
        batch.drop_constraint(CONSTRAINT_NAME, type_="unique")
