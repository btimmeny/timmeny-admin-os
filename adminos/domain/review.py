from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.capabilities.config import (
    ACTION_VALUES,
    DESTINATION_PARAM,
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
from adminos.domain.decisions import (
    HUMAN_ACTOR,
    RULE_ACTOR_PREFIX,
    DecisionKind,
    ItemState,
)
from adminos.domain.learning import record_learning
from adminos.domain.mailboxes import DEFAULT_SCOPE, ReviewScope, capability_scope
from adminos.domain.rules import LearnedRule, read_active_rules
from adminos.logging import get_logger


SCOPE_ACTOR_PREFIX = "scope:"
"""Marks a row taken off the table by where the thread is, not by a decision."""

POLICY_SOURCE = "policy"
DEFAULT_SOURCE = "default"
AI_SOURCE = "ai"
LEARNED_SOURCE = "learned_rule"

__all__ = ["DecisionKind", "HUMAN_ACTOR", "ItemState", "RULE_ACTOR_PREFIX"]

logger = get_logger(__name__)


class ReviewError(RuntimeError):
    """Raised when a review operation is not valid for the current state."""


class ReviewNotFound(ReviewError):
    """Raised when a run, group, or item does not exist."""


class DecisionRefused(ReviewError):
    """Raised when configuration forbids the decision that was asked for."""


@dataclass(frozen=True)
class IneligibleItem:
    """One row a bulk decision could not be applied to, and why not."""

    item_id: str
    thread_id: str
    subject: str | None
    reason: str


class BulkDecisionRefused(DecisionRefused):
    """Raised when any selected row refuses the decision, so none is applied.

    Carries every offending row rather than the first, because "trash 2, 4 and
    7" is answered usefully only by naming which of them cannot be trashed.
    """

    def __init__(self, ineligible: Sequence[IneligibleItem]) -> None:
        self.ineligible = list(ineligible)
        names = ", ".join(entry.item_id for entry in self.ineligible)
        super().__init__(
            f"{len(self.ineligible)} of the selected items do not permit that "
            f"decision, so none was recorded: {names}."
        )


class RunState(StrEnum):
    IN_PROGRESS = "in_progress"
    AWAITING_ACTIONS = "awaiting_actions"
    COMPLETED = "completed"


class GroupState(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_ACTIONS = "awaiting_actions"
    COMPLETED = "completed"


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
    automatable: bool = False
    params: JsonObject = field(default_factory=dict)
    """What the recommendation needs to be actionable, such as the folder to
    file the thread in. Carried through approval so that agreeing with a
    recommendation takes exactly the action that was shown."""


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
    scope: ReviewScope = DEFAULT_SCOPE,
) -> RunView:
    """Return today's review, creating it or topping it up with new mail.

    Resuming is the normal case: a second "start my review" on the same day
    returns the same run, keeps decisions already made, and adds only threads
    that have arrived since.

    A run belongs to one scope. "Show me my archive" is therefore a second run
    of the same day rather than an addition to the first, so that a request to
    see mail outside the inbox cannot widen the review that was already made.
    """
    moment = now or datetime.now(UTC)
    day = review_date or moment.date()

    run = session.execute(
        select(ReviewRun).where(
            ReviewRun.review_date == day,
            ReviewRun.channel == loaded.channel,
            ReviewRun.scope_name == scope.name,
        )
    ).scalar_one_or_none()

    if run is None:
        run = ReviewRun(
            review_date=day,
            channel=loaded.channel,
            scope_name=scope.name,
            scope=scope.as_json(),
            state=RunState.IN_PROGRESS,
            config_version=loaded.version,
            config_digest=loaded.digest,
        )
        session.add(run)
        session.flush()
        logger.info(
            "review run opened for %s in the %s scope on capabilities %s",
            day,
            scope.name,
            loaded.version,
        )

    for capability in loaded.enabled():
        watched = capability_scope(scope, capability)
        group = get_or_create_group(session, run, capability)
        withdraw_out_of_scope(session, run, group, capability, watched, moment)
        populate_group(
            session,
            run,
            group,
            capability,
            moment,
            read_active_rules(session, capability),
            watched,
        )

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
    learned_rules: Sequence[LearnedRule] = (),
    scope: ReviewScope = DEFAULT_SCOPE,
) -> int:
    """Add this capability's outstanding evidence to the group. Returns the count.

    A thread already decided in an earlier run stays out unless its content has
    changed since: a reply reopens a conversation, but a thread that merely sat
    in the inbox does not come back to be dismissed twice.

    Every thread is checked against the scope as it goes in, using the labels it
    carried when it was last seen. Evidence outlives a review, so a thread that
    has since been archived or trashed still has a row here; the scope, not the
    row's existence, is what decides whether it is reviewed again.
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
        if not scope.admits(evidence.label_ids):
            continue
        if evidence.content_hash is not None and settled.get(evidence.id) == evidence.content_hash:
            continue

        outcome = evaluate_policy(capability, evidence, now, learned_rules)
        item = ReviewItem(
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
            recommendation_params=outcome.params or None,
            policy_version=capability.recommendation_policy.version,
            rule_id=outcome.rule_id,
            objective_keys=outcome.objective_keys,
            provenance={
                "capability": capability.key,
                "policy_version": capability.recommendation_policy.version,
                "source_system": evidence.source_system,
            },
        )
        session.add(item)
        if outcome.automatable and outcome.rule_id is not None:
            session.flush()
            authorise_by_rule(session, capability, run, item, outcome, now)
        added += 1

    if added:
        session.flush()
    return added


def withdraw_out_of_scope(
    session: Session,
    run: ReviewRun,
    group: ReviewGroup,
    capability: CapabilityConfig,
    scope: ReviewScope,
    now: datetime,
) -> int:
    """Take undecided rows off the table once their thread has left the scope.

    Archiving a thread in Gmail is Brian saying he is done with it, and it
    should not still be sitting in a review he resumes an hour later asking to
    be decided again. The row is deferred rather than dismissed: nothing was
    decided about the mail, and if the thread comes back to the inbox it is
    reviewable again.

    Only a thread known to have left is withdrawn. A row whose labels have
    never been read says nothing about where the thread is, and guessing there
    would empty a review on the strength of missing information.
    """
    labels = {
        evidence.id: evidence.label_ids
        for evidence in read_capability_evidence(session, capability.key)
        if evidence.label_ids is not None
    }

    withdrawn = 0
    for item in read_group_items(session, group):
        if item.state != ItemState.PENDING:
            continue
        if item.evidence_id not in labels:
            continue
        if scope.admits(labels[item.evidence_id]):
            continue

        item.state = ItemState.DEFERRED
        item.decided_at = now
        session.add(
            ReviewDecision(
                run_id=run.id,
                item_id=item.id,
                capability_key=capability.key,
                decision=DecisionKind.DEFER.value,
                action=None,
                action_params=None,
                followed_recommendation=False,
                recommendation=item.recommendation,
                actor=f"{SCOPE_ACTOR_PREFIX}{scope.name}",
                batch_id=None,
                learning_scope=capability.learning.scope,
                note=(
                    f"Left the {scope.name} scope before it was decided, so it is no "
                    "longer part of this review."
                ),
            )
        )
        withdrawn += 1

    if withdrawn:
        session.flush()
        logger.info(
            "%d items left the %s scope and were withdrawn from %s",
            withdrawn,
            scope.name,
            capability.key,
        )
    return withdrawn


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


def authorise_by_rule(
    session: Session,
    capability: CapabilityConfig,
    run: ReviewRun,
    item: ReviewItem,
    outcome: PolicyOutcome,
    now: datetime,
) -> None:
    """Approve an item because a promoted rule says so, and record who did.

    This is the only approval nobody was asked for, and the path to it is
    narrow: the rule must have been separately promoted to `automatable`, and
    executing it still has to pass the capability's execution permission and
    the global kill switch.
    """
    action = ActionKind(outcome.recommendation)
    item.state = ItemState.APPROVED
    item.approved_action = action.value
    item.approved_params = dict(outcome.params)
    item.decided_at = now

    session.add(
        ReviewDecision(
            run_id=run.id,
            item_id=item.id,
            capability_key=capability.key,
            decision=DecisionKind.APPROVE.value,
            action=action.value,
            action_params=dict(outcome.params) or None,
            followed_recommendation=True,
            recommendation=item.recommendation,
            actor=f"{RULE_ACTOR_PREFIX}{outcome.rule_id}",
            batch_id=None,
            learning_scope=capability.learning.scope,
            note=f"Approved by automatable rule {outcome.rule_id}.",
        )
    )
    session.flush()


def evaluate_policy(
    capability: CapabilityConfig,
    evidence: Evidence,
    now: datetime,
    learned_rules: Sequence[LearnedRule] = (),
) -> PolicyOutcome:
    """Apply the capability's rules to one thread. First match wins.

    Confirmed learned rules are tried before the shipped ones, because a
    learned rule exists only where someone corrected the shipped behaviour. An
    unmatched thread falls to the configured default rather than a guess.
    """
    policy = capability.recommendation_policy
    if capability.playbook.allows(PlaybookStep.RECOMMEND):
        for learned in learned_rules:
            if not matches(learned.match, evidence, now):
                continue
            return PolicyOutcome(
                recommendation=learned.action.value,
                confidence=learned.confidence,
                rationale=learned.rationale,
                source=LEARNED_SOURCE,
                rule_id=learned.id,
                objective_keys=list(capability.objectives.default_keys),
                automatable=learned.automatable,
                params=dict(learned.params),
            )
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
                params=rule.params(),
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

    params = action_params if action_params is not None else recommended_params(item, decision)
    chosen = check_decision(capability, item, decision, action, params)

    if chosen is not None:
        item.state = ItemState.APPROVED
        item.approved_action = chosen.value
        item.approved_params = params or {}
    elif decision is DecisionKind.DEFER:
        item.state = ItemState.DEFERRED
        item.approved_action = None
        item.approved_params = None
    else:
        item.state = ItemState.DISMISSED
        item.approved_action = None
        item.approved_params = None

    item.decided_at = moment

    record = ReviewDecision(
        run_id=run.id,
        item_id=item.id,
        capability_key=capability.key,
        decision=decision.value,
        action=chosen.value if chosen else None,
        action_params=params if capability.learning.record_decisions else None,
        followed_recommendation=followed_recommendation(item, decision, chosen),
        recommendation=item.recommendation,
        actor=actor,
        batch_id=batch_id,
        learning_scope=capability.learning.scope,
        note=note if capability.learning.record_decisions else None,
    )
    session.add(record)
    session.flush()
    record_learning(session, capability, run, item, record, now=moment)
    return item


def recommended_params(item: ReviewItem, decision: DecisionKind) -> JsonObject:
    """The parameters an approval inherits when the caller names none.

    Approving is agreeing with what was shown, and what was shown named a
    folder: "yes" must file the thread there rather than ask again.
    """
    if decision is not DecisionKind.APPROVE:
        return {}
    return dict(item.recommendation_params or {})


def check_decision(
    capability: CapabilityConfig,
    item: ReviewItem,
    decision: DecisionKind,
    action: ActionKind | None,
    action_params: JsonObject | None,
) -> ActionKind | None:
    """Refuse a decision configuration does not permit, and name the action it authorises.

    Separated from applying it so a bulk decision can be checked across every
    item before any of them changes. ``action_params`` is what the caller is
    actually asking for; ``None`` means the caller is only asking whether the
    decision is available at all, and has not chosen a folder yet.
    """
    if not capability.playbook.allows(PlaybookStep.AWAIT_DECISION):
        raise DecisionRefused(f"{capability.key!r} does not take decisions in its playbook.")

    if item.state in TERMINAL_ITEM_STATES and not restores_trash(item, decision, action):
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
    if chosen is ActionKind.GMAIL_MOVE:
        if action_params is None:
            if not capability.gmail.destinations:
                raise DecisionRefused(f"{capability.key!r} has nowhere to file mail.")
        else:
            check_destination(capability, action_params)
    return chosen


def restores_trash(
    item: ReviewItem,
    decision: DecisionKind,
    action: ActionKind | None,
) -> bool:
    """Whether this decision is taking a trashed thread back out of Trash.

    The one thing that may be decided about a settled item, because the
    settlement is the thing being undone. Restoring is not re-deciding what to
    do with a thread: it is reversing what was already done to it.
    """
    return (
        decision is DecisionKind.OVERRIDE
        and action is ActionKind.GMAIL_UNTRASH
        and item.state == ItemState.EXECUTED
        and item.approved_action == ActionKind.GMAIL_TRASH.value
    )


def check_destination(capability: CapabilityConfig, params: JsonObject) -> str:
    """The folder a move files the thread in, or a refusal naming the choices.

    A destination outside the capability's list is refused here rather than at
    execution, so "file it under Career/Citi" fails while it is still a
    sentence and not yet a mailbox change.
    """
    destination = params.get(DESTINATION_PARAM)
    allowed = capability.gmail.destinations
    if not isinstance(destination, str) or not destination.strip():
        raise DecisionRefused(
            "A move must name the folder to file the thread in: "
            f"{', '.join(allowed) or 'no folder is configured'}."
        )
    destination = destination.strip()
    if destination not in allowed:
        raise DecisionRefused(
            f"{capability.key!r} does not file mail in {destination!r}. Its folders "
            f"are: {', '.join(allowed) or 'none'}."
        )
    return destination


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
    action_params: JsonObject | None = None,
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
        missing = sorted(set(item_ids) - {item.id for item in selected})
        if missing:
            raise ReviewNotFound(
                f"Group {group.capability_key!r} has no item {', '.join(repr(m) for m in missing)}."
            )
    if item_ids is None:
        selected = [item for item in selected if item.state == ItemState.PENDING]

    ineligible: list[IneligibleItem] = []
    for item in selected:
        try:
            check_decision(
                capability,
                item,
                decision,
                action,
                action_params
                if action_params is not None
                else recommended_params(item, decision),
            )
        except DecisionRefused as refusal:
            ineligible.append(
                IneligibleItem(
                    item_id=item.id,
                    thread_id=item.source_thread_id,
                    subject=item.subject,
                    reason=str(refusal),
                )
            )
    if ineligible:
        raise BulkDecisionRefused(ineligible)

    return [
        record_decision(
            session,
            capability,
            run,
            item,
            decision,
            action=action,
            action_params=action_params,
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
