from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.capabilities.config import (
    ACTION_VALUES,
    ActionKind,
    CapabilityConfig,
    LoadedCapabilities,
    MatchRule,
    PlaybookStep,
    Recommendation,
)
from adminos.db.models import (
    Evidence,
    JsonObject,
    ReviewDecision,
    ReviewGroup,
    ReviewItem,
    ReviewRun,
)
from adminos.logging import get_logger


HUMAN_ACTOR = "human"
POLICY_SOURCE = "policy"
DEFAULT_SOURCE = "default"
AI_SOURCE = "ai"

logger = get_logger(__name__)


class ReviewError(RuntimeError):
    """Raised when a review operation is not valid for the current state."""


class ReviewNotFound(ReviewError):
    """Raised when a run, group, or item does not exist."""


class DecisionRefused(ReviewError):
    """Raised when configuration forbids the decision that was asked for."""


class RunState(StrEnum):
    IN_PROGRESS = "in_progress"
    AWAITING_ACTIONS = "awaiting_actions"
    COMPLETED = "completed"


class GroupState(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_ACTIONS = "awaiting_actions"
    COMPLETED = "completed"


class ItemState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    FAILED = "failed"
    DISMISSED = "dismissed"
    DEFERRED = "deferred"


class DecisionKind(StrEnum):
    APPROVE = "approve"
    OVERRIDE = "override"
    DISMISS = "dismiss"
    DEFER = "defer"


TERMINAL_ITEM_STATES = {ItemState.DISMISSED, ItemState.EXECUTED}
"""States that settle a thread for good.

Deferral is deliberately not one of them: it clears an item out of today's
review and lets the same thread return tomorrow.
"""

OPEN_ITEM_STATES = {ItemState.PENDING, ItemState.APPROVED, ItemState.FAILED}


@dataclass(frozen=True)
class PolicyOutcome:
    """What a capability's deterministic policy says about one thread."""

    recommendation: str
    confidence: float
    rationale: str
    source: str
    rule_id: str | None
    objective_keys: list[str]


@dataclass(frozen=True)
class Assessment:
    """A schema-validated interpretation supplied by the model."""

    category: str
    confidence: float
    rationale: str
    model_version: str
    recommendation: str | None = None


@dataclass(frozen=True)
class GroupView:
    group: ReviewGroup
    capability: CapabilityConfig
    items: list[ReviewItem]


@dataclass(frozen=True)
class RunView:
    run: ReviewRun
    groups: list[GroupView]
    warnings: list[str] = field(default_factory=list)

    def current_group(self) -> GroupView | None:
        """The first group still needing a decision, in configured order.

        A group waiting only on execution is not what to work on next: the
        review moves to the next group that needs Brian, and comes back to the
        outstanding actions once every group has been decided.
        """
        for view in self.groups:
            if view.group.state in {GroupState.PENDING, GroupState.IN_PROGRESS}:
                return view
        for view in self.groups:
            if view.group.state == GroupState.AWAITING_ACTIONS:
                return view
        return None


def start_or_resume_review(
    session: Session,
    loaded: LoadedCapabilities,
    review_date: date | None = None,
    now: datetime | None = None,
) -> RunView:
    """Return today's review, creating it or topping it up with new mail.

    Resuming is the normal case: a second "start my review" on the same day
    returns the same run, keeps decisions already made, and adds only threads
    that have arrived since.
    """
    moment = now or datetime.now(UTC)
    day = review_date or moment.date()

    run = session.execute(
        select(ReviewRun).where(
            ReviewRun.review_date == day,
            ReviewRun.channel == loaded.channel,
        )
    ).scalar_one_or_none()

    if run is None:
        run = ReviewRun(
            review_date=day,
            channel=loaded.channel,
            state=RunState.IN_PROGRESS,
            config_version=loaded.version,
            config_digest=loaded.digest,
        )
        session.add(run)
        session.flush()
        logger.info("review run opened for %s on capabilities %s", day, loaded.version)

    for capability in loaded.enabled():
        group = get_or_create_group(session, run, capability)
        populate_group(session, run, group, capability, moment)

    session.flush()
    return refresh_states(session, loaded, run)


def get_or_create_group(
    session: Session,
    run: ReviewRun,
    capability: CapabilityConfig,
) -> ReviewGroup:
    group = session.execute(
        select(ReviewGroup).where(
            ReviewGroup.run_id == run.id,
            ReviewGroup.capability_key == capability.key,
        )
    ).scalar_one_or_none()

    if group is None:
        group = ReviewGroup(
            run_id=run.id,
            capability_key=capability.key,
            capability_name=capability.name,
            policy_version=capability.recommendation_policy.version,
            position=capability.position,
            state=GroupState.PENDING,
        )
        session.add(group)
        session.flush()
    return group


def populate_group(
    session: Session,
    run: ReviewRun,
    group: ReviewGroup,
    capability: CapabilityConfig,
    now: datetime,
) -> int:
    """Add this capability's outstanding evidence to the group. Returns the count.

    A thread already decided in an earlier run stays out unless its content has
    changed since: a reply reopens a conversation, but a thread that merely sat
    in the inbox does not come back to be dismissed twice.
    """
    if not capability.playbook.allows(PlaybookStep.COLLECT_EVIDENCE):
        return 0

    present = set(
        session.execute(select(ReviewItem.evidence_id).where(ReviewItem.group_id == group.id))
        .scalars()
        .all()
    )
    settled = read_settled_hashes(session, capability.key)

    added = 0
    for evidence in read_capability_evidence(session, capability.key):
        if evidence.id in present:
            continue
        if evidence.content_hash is not None and settled.get(evidence.id) == evidence.content_hash:
            continue

        outcome = evaluate_policy(capability, evidence, now)
        session.add(
            ReviewItem(
                run_id=run.id,
                group_id=group.id,
                evidence_id=evidence.id,
                source_thread_id=evidence.source_thread_id,
                evidence_hash=evidence.content_hash,
                subject=evidence.subject,
                participants=evidence.participants,
                received_at=evidence.received_at,
                state=ItemState.PENDING,
                recommendation=outcome.recommendation,
                recommendation_source=outcome.source,
                recommendation_confidence=outcome.confidence,
                recommendation_rationale=outcome.rationale,
                policy_version=capability.recommendation_policy.version,
                rule_id=outcome.rule_id,
                objective_keys=outcome.objective_keys,
                provenance={
                    "capability": capability.key,
                    "policy_version": capability.recommendation_policy.version,
                    "source_system": evidence.source_system,
                },
            )
        )
        added += 1

    if added:
        session.flush()
    return added


def read_capability_evidence(session: Session, capability_key: str) -> list[Evidence]:
    """Evidence attributed to one capability, newest first.

    Attribution is a JSON list rather than a column filter because a thread can
    carry two capabilities' labels; filtering happens in Python so the behaviour
    is identical on SQLite and PostgreSQL.
    """
    rows = (
        session.execute(select(Evidence).order_by(Evidence.received_at.desc().nullslast()))
        .scalars()
        .all()
    )
    return [row for row in rows if capability_key in read_capability_keys(row)]


def read_capability_keys(evidence: Evidence) -> list[str]:
    keys = evidence.capability_keys
    if not isinstance(keys, list):
        return []
    return [key for key in keys if isinstance(key, str)]


def read_settled_hashes(session: Session, capability_key: str) -> dict[str, str]:
    """The content hash each already-settled thread had when it was settled."""
    rows = session.execute(
        select(ReviewItem)
        .join(ReviewGroup, ReviewGroup.id == ReviewItem.group_id)
        .where(ReviewGroup.capability_key == capability_key)
        .order_by(ReviewItem.created_at)
    ).scalars()

    settled: dict[str, str] = {}
    for item in rows:
        if item.state in TERMINAL_ITEM_STATES and item.evidence_hash is not None:
            settled[item.evidence_id] = item.evidence_hash
    return settled


def evaluate_policy(
    capability: CapabilityConfig,
    evidence: Evidence,
    now: datetime,
) -> PolicyOutcome:
    """Apply the capability's rules to one thread. First match wins.

    Deterministic by construction: the rules read only retained metadata, and
    an unmatched thread falls to the configured default rather than a guess.
    """
    policy = capability.recommendation_policy
    if capability.playbook.allows(PlaybookStep.RECOMMEND):
        for rule in policy.rules:
            if not matches(rule.when, evidence, now):
                continue
            return PolicyOutcome(
                recommendation=rule.recommend,
                confidence=rule.confidence,
                rationale=rule.rationale,
                source=POLICY_SOURCE,
                rule_id=rule.id,
                objective_keys=rule.aligns_with or list(capability.objectives.default_keys),
            )

    return PolicyOutcome(
        recommendation=policy.default,
        confidence=0.0,
        rationale=f"No rule in {policy.version} matched, so {capability.name} defers to review.",
        source=DEFAULT_SOURCE,
        rule_id=None,
        objective_keys=list(capability.objectives.default_keys),
    )


def matches(rule: MatchRule, evidence: Evidence, now: datetime) -> bool:
    """Whether every stated condition holds. Conditions AND, values OR."""
    subject = (evidence.subject or "").casefold()
    if rule.subject_contains and not any(
        term.casefold() in subject for term in rule.subject_contains
    ):
        return False

    participants = [
        address.casefold()
        for address in (evidence.participants or [])
        if isinstance(address, str)
    ]
    if rule.participants and not any(
        address in participants for address in (value.casefold() for value in rule.participants)
    ):
        return False
    if rule.participant_domains and not any(
        address.endswith(f"@{domain.casefold().lstrip('@')}")
        for address in participants
        for domain in rule.participant_domains
    ):
        return False

    age = thread_age(evidence, now)
    if rule.older_than_days is not None:
        if age is None or age < timedelta(days=rule.older_than_days):
            return False
    if rule.newer_than_days is not None:
        if age is None or age > timedelta(days=rule.newer_than_days):
            return False

    return True


def thread_age(evidence: Evidence, now: datetime) -> timedelta | None:
    if evidence.received_at is None:
        return None
    received_at = evidence.received_at
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    return now - received_at


def record_assessment(
    session: Session,
    capability: CapabilityConfig,
    item: ReviewItem,
    assessment: Assessment,
) -> ReviewItem:
    """Store the model's reading of one thread, within the policy's limits.

    The model may narrow what a thread is and suggest what to do about it. It
    cannot widen what the capability is allowed to do, and a suggestion below
    the configured confidence floor is recorded but not adopted.
    """
    policy = capability.recommendation_policy
    if assessment.category not in policy.categories:
        raise DecisionRefused(
            f"{assessment.category!r} is not a category {capability.key!r} recognises."
        )
    if assessment.recommendation is not None:
        if not policy.allow_ai_recommendation:
            raise DecisionRefused(f"{capability.key!r} does not accept model recommendations.")
        if assessment.recommendation in ACTION_VALUES and not capability.permits(
            ActionKind(assessment.recommendation)
        ):
            raise DecisionRefused(
                f"{capability.key!r} is not allowed to {assessment.recommendation!r}."
            )

    item.category = assessment.category
    item.model_version = assessment.model_version
    item.provenance = {
        **(item.provenance if isinstance(item.provenance, dict) else {}),
        "assessed_by": assessment.model_version,
        "assessed_confidence": assessment.confidence,
    }

    adopted = (
        assessment.recommendation is not None
        and item.state == ItemState.PENDING
        and assessment.confidence >= policy.min_ai_confidence
    )
    if adopted:
        item.recommendation = str(assessment.recommendation)
        item.recommendation_source = AI_SOURCE
        item.recommendation_confidence = assessment.confidence
        item.recommendation_rationale = assessment.rationale
    elif assessment.recommendation is not None:
        item.provenance = {
            **item.provenance,
            "unadopted_recommendation": assessment.recommendation,
            "unadopted_because": (
                f"confidence {assessment.confidence} is below the "
                f"{policy.min_ai_confidence} {capability.key!r} requires"
                if item.state == ItemState.PENDING
                else f"the item is already {item.state}"
            ),
        }

    session.flush()
    return item


def record_decision(
    session: Session,
    capability: CapabilityConfig,
    run: ReviewRun,
    item: ReviewItem,
    decision: DecisionKind,
    action: ActionKind | None = None,
    action_params: JsonObject | None = None,
    note: str | None = None,
    actor: str = HUMAN_ACTOR,
    batch_id: str | None = None,
    now: datetime | None = None,
) -> ReviewItem:
    """Apply one human decision to one item, or refuse it.

    Approval is the only route to an action, and configuration decides whether
    the action is permitted at all: the caller cannot name an action the
    capability is not allowed to take, however it is phrased.
    """
    moment = now or datetime.now(UTC)

    chosen = check_decision(capability, item, decision, action)

    if chosen is not None:
        item.state = ItemState.APPROVED
        item.approved_action = chosen.value
        item.approved_params = action_params or {}
    elif decision is DecisionKind.DEFER:
        item.state = ItemState.DEFERRED
        item.approved_action = None
        item.approved_params = None
    else:
        item.state = ItemState.DISMISSED
        item.approved_action = None
        item.approved_params = None

    item.decided_at = moment

    session.add(
        ReviewDecision(
            run_id=run.id,
            item_id=item.id,
            capability_key=capability.key,
            decision=decision.value,
            action=chosen.value if chosen else None,
            action_params=action_params if capability.learning.record_decisions else None,
            followed_recommendation=followed_recommendation(item, decision, chosen),
            recommendation=item.recommendation,
            actor=actor,
            batch_id=batch_id,
            learning_scope=capability.learning.scope,
            note=note if capability.learning.record_decisions else None,
        )
    )
    session.flush()
    return item


def check_decision(
    capability: CapabilityConfig,
    item: ReviewItem,
    decision: DecisionKind,
    action: ActionKind | None,
) -> ActionKind | None:
    """Refuse a decision configuration does not permit, and name the action it authorises.

    Separated from applying it so a bulk decision can be checked across every
    item before any of them changes.
    """
    if not capability.playbook.allows(PlaybookStep.AWAIT_DECISION):
        raise DecisionRefused(f"{capability.key!r} does not take decisions in its playbook.")
    if item.state in TERMINAL_ITEM_STATES:
        raise DecisionRefused(f"Item {item.id} is already {item.state} and cannot be re-decided.")

    chosen = resolve_action(capability, item, decision, action)
    if chosen is None:
        return None

    if not capability.playbook.allows(PlaybookStep.EXECUTE_APPROVED):
        raise DecisionRefused(
            f"{capability.key!r} has no execute_approved step, so {chosen.value!r} "
            "cannot be approved."
        )
    if capability.objectives.require_alignment and not item.objective_keys:
        raise DecisionRefused(
            f"{capability.key!r} requires an objective for every action, and this item has none."
        )
    return chosen


def followed_recommendation(
    item: ReviewItem,
    decision: DecisionKind,
    chosen: ActionKind | None,
) -> bool:
    """Whether the human agreed with what was recommended.

    Recorded because disagreement is the signal worth learning from, and it is
    only legible if agreement is recorded too.
    """
    if chosen is not None:
        return chosen.value == item.recommendation
    return decision is DecisionKind.DISMISS and item.recommendation == Recommendation.NO_ACTION


def resolve_action(
    capability: CapabilityConfig,
    item: ReviewItem,
    decision: DecisionKind,
    action: ActionKind | None,
) -> ActionKind | None:
    """Turn a decision into the action it authorises, if any."""
    if decision is DecisionKind.APPROVE:
        if item.recommendation not in ACTION_VALUES:
            raise DecisionRefused(
                f"There is nothing to approve: the recommendation is "
                f"{item.recommendation!r}. Send an override with an explicit action."
            )
        chosen = ActionKind(item.recommendation)
    elif decision is DecisionKind.OVERRIDE:
        if action is None:
            raise DecisionRefused("An override must name the action to take instead.")
        chosen = action
    else:
        return None

    if not capability.permits(chosen):
        raise DecisionRefused(f"{capability.key!r} is not allowed to {chosen.value!r}.")
    return chosen


def decide_group(
    session: Session,
    capability: CapabilityConfig,
    run: ReviewRun,
    group: ReviewGroup,
    decision: DecisionKind,
    item_ids: Sequence[str] | None = None,
    action: ActionKind | None = None,
    note: str | None = None,
    batch_id: str | None = None,
    now: datetime | None = None,
) -> list[ReviewItem]:
    """Apply one decision to many items in a group, atomically.

    Either every item is decided or none is: a bulk approval that would take a
    forbidden action on one thread must not half-apply to the others.
    """
    if not capability.approval.allow_bulk_decisions:
        raise DecisionRefused(f"{capability.key!r} does not allow bulk decisions.")

    items = read_group_items(session, group)
    selected = [item for item in items if item_ids is None or item.id in set(item_ids)]
    if item_ids is not None:
        missing = set(item_ids) - {item.id for item in selected}
        if missing:
            raise ReviewNotFound(f"Group {group.capability_key!r} has no item {missing.pop()!r}.")
    if item_ids is None:
        selected = [item for item in selected if item.state == ItemState.PENDING]

    for item in selected:
        check_decision(capability, item, decision, action)

    return [
        record_decision(
            session,
            capability,
            run,
            item,
            decision,
            action=action,
            note=note,
            batch_id=batch_id,
            now=now,
        )
        for item in selected
    ]


def refresh_states(
    session: Session,
    loaded: LoadedCapabilities,
    run: ReviewRun,
    now: datetime | None = None,
) -> RunView:
    """Recompute group and run state from the items, and return the view."""
    moment = now or datetime.now(UTC)
    views: list[GroupView] = []

    for group in read_groups(session, run):
        capability = loaded.get(group.capability_key)
        items = read_group_items(session, group)
        group.state = group_state(capability, items)
        if group.state != GroupState.PENDING and group.started_at is None:
            group.started_at = moment
        if group.state == GroupState.COMPLETED:
            group.completed_at = group.completed_at or moment
        else:
            group.completed_at = None
        views.append(GroupView(group=group, capability=capability, items=items))

    states = {view.group.state for view in views}
    if states and states == {GroupState.COMPLETED}:
        run.state = RunState.COMPLETED
        run.completed_at = run.completed_at or moment
    elif states and states <= {GroupState.COMPLETED, GroupState.AWAITING_ACTIONS}:
        run.state = RunState.AWAITING_ACTIONS
        run.completed_at = None
    else:
        run.state = RunState.IN_PROGRESS
        run.completed_at = None

    session.flush()
    return RunView(run=run, groups=views)


def group_state(capability: CapabilityConfig, items: Sequence[ReviewItem]) -> GroupState:
    if not items:
        return GroupState.COMPLETED

    undecided = [item for item in items if item.state == ItemState.PENDING]
    unexecuted = [item for item in items if item.state in {ItemState.APPROVED, ItemState.FAILED}]

    if capability.completion.require_all_items_decided and undecided:
        return GroupState.PENDING if len(undecided) == len(items) else GroupState.IN_PROGRESS
    if capability.completion.require_executed_actions and unexecuted:
        return GroupState.AWAITING_ACTIONS
    return GroupState.COMPLETED


def read_groups(session: Session, run: ReviewRun) -> list[ReviewGroup]:
    return list(
        session.execute(
            select(ReviewGroup)
            .where(ReviewGroup.run_id == run.id)
            .order_by(ReviewGroup.position)
        )
        .scalars()
        .all()
    )


def read_group_items(session: Session, group: ReviewGroup) -> list[ReviewItem]:
    return list(
        session.execute(
            select(ReviewItem)
            .where(ReviewItem.group_id == group.id)
            .order_by(ReviewItem.received_at.desc().nullslast(), ReviewItem.created_at)
        )
        .scalars()
        .all()
    )


def read_run(session: Session, run_id: str) -> ReviewRun:
    run = session.get(ReviewRun, run_id)
    if run is None:
        raise ReviewNotFound(f"No review run {run_id!r}.")
    return run


def read_group(session: Session, run: ReviewRun, capability_key: str) -> ReviewGroup:
    group = session.execute(
        select(ReviewGroup).where(
            ReviewGroup.run_id == run.id,
            ReviewGroup.capability_key == capability_key,
        )
    ).scalar_one_or_none()
    if group is None:
        raise ReviewNotFound(f"Run {run.id!r} has no {capability_key!r} group.")
    return group


def read_item(session: Session, run: ReviewRun, item_id: str) -> ReviewItem:
    item = session.get(ReviewItem, item_id)
    if item is None or item.run_id != run.id:
        raise ReviewNotFound(f"Run {run.id!r} has no item {item_id!r}.")
    return item
