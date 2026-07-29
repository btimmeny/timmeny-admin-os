import uuid

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
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
JsonValue = str | int | float | bool | None | list[str] | list[int]
JsonObject = dict[str, JsonValue]
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
    participants: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snippet: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(DIGEST_LENGTH))
    capability_keys: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
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


class ReviewRun(Base):
    """One daily review: the session Brian starts with "good morning".

    Identity is the review date, so "start my review" twice in a day resumes
    rather than restarts.
    """

    __tablename__ = "review_runs"
    __table_args__ = (
        UniqueConstraint("review_date", "channel", name="uq_review_run_date_channel"),
    )

    id: Mapped[str] = id_column()
    review_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    config_version: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(DIGEST_LENGTH), nullable=False)
    started_at: Mapped[datetime] = created_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated_at_column()


class ReviewGroup(Base):
    """One capability's slice of a review run, presented on its own."""

    __tablename__ = "review_groups"
    __table_args__ = (
        UniqueConstraint("run_id", "capability_key", name="uq_review_group_capability"),
    )

    id: Mapped[str] = id_column()
    run_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    capability_key: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    capability_name: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ReviewItem(Base):
    """One thread inside one group, with the recommendation shown for it.

    Evidence is deliberately referenced by id rather than by foreign key, and
    the fields needed to present the item are copied here: archiving a thread
    retires its evidence, and the record of what was decided must outlive that.
    """

    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint("group_id", "evidence_id", name="uq_review_item_evidence"),
        Index("ix_review_items_run_state", "run_id", "state"),
    )

    id: Mapped[str] = id_column()
    run_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_groups.id", ondelete="CASCADE"), nullable=False
    )
    evidence_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    source_thread_id: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    evidence_hash: Mapped[str | None] = mapped_column(String(DIGEST_LENGTH))
    subject: Mapped[str | None] = mapped_column(Text)
    participants: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    recommendation: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    recommendation_source: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    recommendation_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation_rationale: Mapped[str | None] = mapped_column(Text)
    recommendation_params: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    policy_version: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    model_version: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    category: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    objective_keys: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    provenance: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    approved_action: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    approved_params: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewDecision(Base):
    """An append-only record of what a human chose for one item."""

    __tablename__ = "review_decisions"
    __table_args__ = (Index("ix_review_decisions_item", "item_id"),)

    id: Mapped[str] = id_column()
    run_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_items.id", ondelete="CASCADE"), nullable=False
    )
    capability_key: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    decision: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    action: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    action_params: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    followed_recommendation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recommendation: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    actor: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    learning_scope: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class ReviewAction(Base):
    """One external effect authorised by one decision, and its whole lifecycle.

    An action is the only thing that touches Gmail or Monday. It moves
    approved -> prepared -> executed -> verified -> completed, or fails, and
    `idempotency_key` is what stops a retry performing the same write twice.
    """

    __tablename__ = "review_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_review_action_idempotency"),
        Index("ix_review_actions_run_state", "run_id", "state"),
        Index("ix_review_actions_item", "item_id"),
    )

    id: Mapped[str] = id_column()
    run_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_items.id", ondelete="CASCADE"), nullable=False
    )
    capability_key: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    action_kind: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    params: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    prepared_params: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    idempotency_key: Mapped[str] = mapped_column(String(DIGEST_LENGTH), nullable=False)
    target_thread_id: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    external_kind: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    external_ref: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    approval_kind: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionScope(Base):
    """Exactly what was selected for execution, frozen when it was prepared.

    Preparation and execution are separate requests, and between them the only
    thing tying "these nineteen" to what actually runs is this row. It records
    the items asked for, the items prepared, the actions those became, and the
    items left out with the reason. Execution names the scope by id and runs
    its actions, so a narrower selection cannot widen on the way through.
    """

    __tablename__ = "action_scopes"
    __table_args__ = (Index("ix_action_scopes_run", "run_id", "capability_key", "state"),)

    id: Mapped[str] = id_column()
    run_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    capability_key: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    entire_capability: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_item_ids: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    prepared_item_ids: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    action_ids: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    excluded: Mapped[list[dict[str, str]]] = mapped_column(JSON_TYPE, nullable=False)
    executed_item_ids: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    verified_item_ids: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    superseded_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    actor: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionEvent(Base):
    """Append-only audit of everything that happened to one action."""

    __tablename__ = "action_events"
    __table_args__ = (
        UniqueConstraint("action_id", "sequence", name="uq_action_event_sequence"),
    )

    id: Mapped[str] = id_column()
    action_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_actions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    state_after: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    detail: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    external_ref: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    created_at: Mapped[datetime] = created_at_column()


class LearningEvent(Base):
    """A correction worth learning from, recorded as data rather than a rule.

    Written whenever a decision departs from what was recommended. It is
    evidence for a rule someone may later propose; it never becomes one by
    itself.
    """

    __tablename__ = "learning_events"
    __table_args__ = (Index("ix_learning_events_capability", "capability_key", "kind"),)

    id: Mapped[str] = id_column()
    capability_key: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_runs.id", ondelete="SET NULL")
    )
    item_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_items.id", ondelete="SET NULL")
    )
    decision_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    kind: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    recommended: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    chosen: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    recommendation_source: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    model_version: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    signals: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    note: Mapped[str | None] = mapped_column(Text)
    candidate_rule_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    created_at: Mapped[datetime] = created_at_column()


class CandidateRule(Base):
    """A rule the system has noticed, or a human has written, and its standing.

    States run observed -> proposed -> confirmed -> automatable -> retired. Only
    `confirmed` and `automatable` affect recommendations, and only
    `automatable` may act without asking first.
    """

    __tablename__ = "candidate_rules"
    __table_args__ = (
        Index("ix_candidate_rules_capability_state", "capability_key", "state"),
    )

    id: Mapped[str] = id_column()
    capability_key: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    match_conditions: Mapped[JsonObject] = mapped_column("match", JSON_TYPE, nullable=False)
    action: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    action_params: Mapped[JsonObject | None] = mapped_column(JSON_TYPE)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    observed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    policy_version: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    proposed_by: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    automatable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


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
    options: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    selected_option: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
