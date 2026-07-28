import uuid

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
ID_LENGTH = 36
SHORT_TEXT_LENGTH = 255
DIGEST_LENGTH = 64


def generate_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


def id_column() -> Mapped[str]:
    return mapped_column(String(ID_LENGTH), primary_key=True, default=generate_id)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def updated_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class OperationalObject(Base):
    """A durable object that evidence can create, update, complete, or block."""

    __tablename__ = "operational_objects"

    id: Mapped[str] = id_column()
    type: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    life_area: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    parent_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("operational_objects.id")
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class Evidence(Base):
    """A reference to an external artifact. Never executable work in itself.

    Message bodies and attachments are deliberately not stored; see ADR-0003.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("source_system", "source_thread_id", name="uq_evidence_source_thread"),
        Index("ix_evidence_source_thread_id", "source_thread_id"),
    )

    id: Mapped[str] = id_column()
    source_system: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    source_thread_id: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    subject: Mapped[str | None] = mapped_column(Text)
    participants: Mapped[Any | None] = mapped_column(JSON_TYPE)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snippet: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(DIGEST_LENGTH))
    created_at: Mapped[datetime] = created_at_column()


class Classification(Base):
    """An inference about evidence. Reviewable, never stored as confirmed fact."""

    __tablename__ = "classifications"
    __table_args__ = (
        UniqueConstraint(
            "evidence_id", "classifier_version", name="uq_classification_evidence_version"
        ),
    )

    id: Mapped[str] = id_column()
    evidence_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("evidence.id"), nullable=False
    )
    classifier_version: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    matched_object_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("operational_objects.id")
    )
    proposed_object_type: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    relationship_type: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    disposition: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class ExternalMapping(Base):
    """The only place an external system's record id is stored.

    A row is reserved in `pending` before an external create so that a retry can
    recover the item by `admin_os_id` instead of duplicating it; see ADR-0002.
    """

    __tablename__ = "external_mappings"
    __table_args__ = (
        UniqueConstraint(
            "external_system", "external_kind", "external_id", name="uq_external_mapping_external"
        ),
        UniqueConstraint(
            "internal_type",
            "internal_id",
            "external_system",
            "external_kind",
            name="uq_external_mapping_internal",
        ),
        Index("ix_external_mappings_internal", "internal_type", "internal_id"),
    )

    id: Mapped[str] = id_column()
    internal_type: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    internal_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    external_system: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    external_kind: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    board_id: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    admin_os_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), unique=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class WorkflowRun(Base):
    """One execution of one workflow over one piece of evidence."""

    __tablename__ = "workflow_runs"
    __table_args__ = (Index("ix_workflow_runs_state_retry", "state", "next_retry_at"),)

    id: Mapped[str] = id_column()
    workflow_name: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(DIGEST_LENGTH), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    evidence_id: Mapped[str | None] = mapped_column(String(ID_LENGTH), ForeignKey("evidence.id"))
    operational_object_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("operational_objects.id")
    )
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class WorkflowStep(Base):
    """The audit record: what a run read, changed, and verified.

    Digests rather than payloads, so audit history cannot leak secrets or
    message content.
    """

    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_name", "sequence", name="uq_workflow_step_identity"),
    )

    id: Mapped[str] = id_column()
    run_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    request_digest: Mapped[str | None] = mapped_column(String(DIGEST_LENGTH))
    response_digest: Mapped[str | None] = mapped_column(String(DIGEST_LENGTH))
    external_ref: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(Base):
    """An open or resolved choice, recorded when classification is ambiguous."""

    __tablename__ = "decisions"

    id: Mapped[str] = id_column()
    workflow_run_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("workflow_runs.id")
    )
    operational_object_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("operational_objects.id")
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[Any | None] = mapped_column(JSON_TYPE)
    selected_option: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
