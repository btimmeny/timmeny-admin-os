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
JsonDocument = dict[str, object]
"""A nested document stored whole: a playbook, a validation report.

Kept apart from `JsonObject`, which is a flat record of scalars. A playbook is
read back through the model that wrote it, so its shape is enforced there
rather than by the column.
"""
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
    label_ids: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    """Where the thread was when it was last seen, in Gmail's own terms.

    What decides whether a thread belongs in a review: the scope is checked
    against these labels, so a thread that has left the inbox drops out of the
    next review without anything having to remember that it used to be there.
    Null means never seen in any scope, which is not the same as being in one.
    """
    snoozed: Mapped[bool | None] = mapped_column(Boolean)
    """Whether Gmail was holding the thread back when it was last searched for.

    The one fact about a thread's whereabouts that carries no label: it can be
    learned only from a search that asked, so it is recorded when one does.
    Null means no search has said either way, which a review of snoozed mail
    treats as not snoozed rather than as a maybe.
    """
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

    Identity is the review date, the scope, and the revision. A review is a
    snapshot of the mailbox rather than the mailbox itself, so "good morning"
    twice in a day is two snapshots: the second reads Gmail again and the
    first stays as the record of what was decided against it. "Show me my
    archive" is a separate review of a different set of mail rather than a
    change to today's.
    """

    __tablename__ = "review_runs"
    __table_args__ = (
        UniqueConstraint(
            "review_date",
            "channel",
            "scope_name",
            "revision",
            name="uq_review_run_date_channel_scope_revision",
        ),
    )

    id: Mapped[str] = id_column()
    review_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    scope_name: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    """Which attempt at this date and scope this is.

    A day can be reviewed twice: the first review is abandoned and a second
    one opened on refreshed mail. The earlier revision keeps its decisions and
    its history, and the later one starts from what the mailbox says now.
    """
    scope: Mapped[JsonObject] = mapped_column(JSON_TYPE, nullable=False)
    """The scope this run was started with, kept so it can be reported exactly."""
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    config_version: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(DIGEST_LENGTH), nullable=False)
    started_at: Mapped[datetime] = created_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the mailbox was last read into this review.

    What makes "resume" and "start again" different questions: a review whose
    evidence was refreshed an hour ago is a stale view of the inbox, and
    saying when it was read is how that can be told without guessing.
    """
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """The mailbox this review is of: when its evidence was first read.

    Unlike `evidence_refresh_at`, which moves whenever Gmail is read again,
    this is the moment the snapshot was taken and does not change. "Is this
    current?" is answered against it.
    """
    supersedes_run_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_runs.id", ondelete="SET NULL")
    )
    """The review this one replaced, where it replaced one.

    A fresh snapshot sets the previous review aside rather than continuing it,
    and the chain says which morning's work the new one is standing in front
    of — decisions made there are still readable, and still that review's.
    """
    updated_at: Mapped[datetime] = updated_at_column()


class ReviewPlan(Base):
    """The order a review will be worked in, agreed before it is worked.

    A plan exists from the moment a review is opened, and is `proposed` until
    Brian has seen it and said to begin: the groups, their sizes and what will
    happen to each are the operating contract for the morning, and a review
    that starts presenting rows before it has stated its plan has made that
    contract implicit again.

    `sequence` is the whole order including anything set aside, and `skipped`
    says which of those are not for today, so "skip Legal" is recorded as a
    decision about this review rather than as a group that never existed.
    """

    __tablename__ = "review_plans"
    __table_args__ = (UniqueConstraint("run_id", name="uq_review_plan_run"),)

    id: Mapped[str] = id_column()
    run_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    """Raised whenever the order or the set-aside groups change.

    A review worked in an order nobody agreed to is the thing this counts
    against: each change is a version, and the version is reported.
    """
    sequence: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    skipped: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    config_version: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    begun_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    begun_by: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    created_at: Mapped[datetime] = created_at_column()
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


class PlaybookRevision(Base):
    """One version of the playbook, and how it came to be that version.

    The playbook is Brian's operating process, so changing it is a decision
    rather than an edit: a revision is written as `proposed`, read back to him
    as the exact effect, and becomes `active` only when he says so. The one it
    replaces becomes `superseded` and stays readable, because "why did we start
    doing it this way?" is a question with an answer.

    A revision that no longer validates — a capability removed from under it —
    is marked `invalid` rather than run. Only one revision of a playbook is
    active at a time.
    """

    __tablename__ = "playbook_revisions"
    __table_args__ = (
        UniqueConstraint("playbook_id", "number", name="uq_playbook_revision_number"),
        Index("ix_playbook_revisions_status", "playbook_id", "status"),
    )

    id: Mapped[str] = id_column()
    playbook_id: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    """Which revision of this playbook it is, counting from one.

    The id is what everything refers to; this is what a person says out loud.
    """
    status: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    document: Mapped[JsonDocument] = mapped_column(JSON_TYPE, nullable=False)
    """The whole playbook as it stood, not a diff against another revision.

    Stored whole so that reading an old revision never depends on replaying
    every change since, and so a revision cannot be changed by something it
    was written before.
    """
    validation: Mapped[JsonDocument | None] = mapped_column(JSON_TYPE)
    change_summary: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    """What this revision does, in the sentences it was confirmed by."""
    rationale: Mapped[str | None] = mapped_column(Text)
    based_on_revision_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("playbook_revisions.id", ondelete="SET NULL")
    )
    created_by: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated_at_column()


class AssistantSession(Base):
    """One working session: a run of the playbook against the state of the day.

    The playbook says what is worked through; the session is the working. It
    holds the revision it opened with for its whole life, so a playbook changed
    at half past nine does not rearrange a session already under way.

    Overrides are how "skip Legal today" stays about today: they are recorded
    here, on the session, and never reach the playbook.
    """

    __tablename__ = "assistant_sessions"
    __table_args__ = (Index("ix_assistant_sessions_status", "status", "opened_at"),)

    id: Mapped[str] = id_column()
    playbook_id: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    playbook_revision_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("playbook_revisions.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    sequence: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    """Every activity this session covers, in the order it will work them."""
    skipped: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    """Those set aside for this session alone."""
    overrides: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    """What was asked for that differs from the playbook, said in sentences."""
    current_activity_key: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    opened_at: Mapped[datetime] = created_at_column()
    begun_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_session_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("assistant_sessions.id", ondelete="SET NULL")
    )
    """The session this one replaced, where waking up replaced one."""
    updated_at: Mapped[datetime] = updated_at_column()


class SessionActivity(Base):
    """One activity within a session, and where it has got to.

    `run_id` is the join to real work: the email review is a review run, and
    the activity is the session's record that it was the thing being worked.
    An activity the playbook names and this service cannot yet perform is
    `unavailable` — said out loud, never quietly dropped.
    """

    __tablename__ = "session_activities"
    __table_args__ = (
        UniqueConstraint("session_id", "activity_key", name="uq_session_activity_key"),
    )

    id: Mapped[str] = id_column()
    session_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("assistant_sessions.id", ondelete="CASCADE"), nullable=False
    )
    activity_key: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    intro: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    """The step keys this activity will work, in order, as agreed at the open."""
    run_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("review_runs.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class Rule(Base):
    """A rule's identity and standing, apart from what it currently says.

    Split from its versions because those are two different lifetimes: what a
    rule is about survives every rewording of it, and the wording has to stay
    readable long after it was replaced. The row points at the version in
    force; the versions are written once and kept.
    """

    __tablename__ = "rules"
    __table_args__ = (
        Index("ix_rules_type_status", "rule_type", "status"),
        Index("ix_rules_capability_status", "capability_key", "status"),
    )

    id: Mapped[str] = id_column()
    rule_type: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    capability_key: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    status: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    """The version in force. Nullable only for the instant before the first one."""
    digest: Mapped[str] = mapped_column(String(DIGEST_LENGTH), nullable=False)
    """What the rule is about and does, hashed, so the same rule is not proposed twice."""
    created_by: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RuleVersion(Base):
    """One immutable statement of what a rule matches and does.

    Nothing here is updated after it is written. A review records the version
    it ran under, so what a morning was worked by stays readable even after
    the rule has been rewritten twice.
    """

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "number", name="uq_rule_version_number"),)

    id: Mapped[str] = id_column()
    rule_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    match_conditions: Mapped[JsonDocument] = mapped_column("match", JSON_TYPE, nullable=False)
    effects: Mapped[list[JsonDocument]] = mapped_column(JSON_TYPE, nullable=False)
    constraints: Mapped[JsonDocument] = mapped_column(JSON_TYPE, nullable=False)
    examples: Mapped[JsonDocument | None] = mapped_column(JSON_TYPE)
    summary: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    """The rule as sentences, generated from it at the moment it was written."""
    digest: Mapped[str] = mapped_column(String(DIGEST_LENGTH), nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("rule_versions.id", ondelete="SET NULL")
    )
    created_by: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class RuleEvent(Base):
    """Something that happened to a rule, in the order it happened.

    Every move it made and who made it, kept separately from the rule so that
    a status is never the only account of how it got there.
    """

    __tablename__ = "rule_events"
    __table_args__ = (Index("ix_rule_events_rule", "rule_id", "created_at"),)

    id: Mapped[str] = id_column()
    rule_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[str | None] = mapped_column(
        String(ID_LENGTH), ForeignKey("rule_versions.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    to_status: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    actor: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    detail: Mapped[JsonDocument | None] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = created_at_column()
