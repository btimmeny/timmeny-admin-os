from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.capabilities.config import (
    ACTION_VALUES,
    DESTINATION_PARAM,
    ActionKind,
    CapabilityConfig,
    MatchRule,
)
from adminos.db.models import (
    CandidateRule,
    JsonObject,
    LearningEvent,
    ReviewDecision,
    ReviewItem,
    ReviewRun,
)
from adminos.domain.decisions import DecisionKind
from adminos.domain.rules import LEARNED_SOURCE, RuleRefused, RuleState, record_rule
from adminos.logging import get_logger


NO_LEARNING = "none"

logger = get_logger(__name__)


class LearningKind(StrEnum):
    OVERRIDE = "override"
    DISMISSED_RECOMMENDATION = "dismissed_recommendation"
    DEFERRED_RECOMMENDATION = "deferred_recommendation"
    CONFIRMED_RECOMMENDATION = "confirmed_recommendation"


CORRECTIONS = {
    LearningKind.OVERRIDE,
    LearningKind.DISMISSED_RECOMMENDATION,
    LearningKind.DEFERRED_RECOMMENDATION,
}
"""The kinds worth proposing a rule from: the ones where Brian disagreed."""


@dataclass(frozen=True)
class LearningOutcome:
    event: LearningEvent
    candidate_rule: CandidateRule | None


def classify_decision(decision: ReviewDecision) -> LearningKind:
    """What a decision says about the recommendation it answered.

    Dismissing a thread the review had nothing to suggest for is agreement,
    not correction: only turning down a proposed action is a disagreement.
    """
    if decision.decision == DecisionKind.OVERRIDE:
        return LearningKind.OVERRIDE
    if decision.decision == DecisionKind.DEFER:
        return LearningKind.DEFERRED_RECOMMENDATION
    if decision.decision == DecisionKind.DISMISS and decision.recommendation in ACTION_VALUES:
        return LearningKind.DISMISSED_RECOMMENDATION
    return LearningKind.CONFIRMED_RECOMMENDATION


def record_learning(
    session: Session,
    capability: CapabilityConfig,
    run: ReviewRun,
    item: ReviewItem,
    decision: ReviewDecision,
    now: datetime | None = None,
) -> LearningOutcome | None:
    """Record what a decision taught, and notice a rule it might imply.

    A correction is stored as an event, and a matching candidate rule is
    recorded as `observed`. Observed rules change nothing: turning one into
    advice takes a proposal and a confirmation, both explicit.
    """
    if capability.learning.scope == NO_LEARNING or not capability.learning.record_decisions:
        return None

    moment = now or datetime.now(UTC)
    kind = classify_decision(decision)
    chosen = decision.action or decision.decision

    event = LearningEvent(
        capability_key=capability.key,
        run_id=run.id,
        item_id=item.id,
        decision_id=decision.id,
        kind=kind.value,
        recommended=item.recommendation,
        chosen=chosen,
        recommendation_source=item.recommendation_source,
        policy_version=item.policy_version,
        rule_id=item.rule_id,
        model_version=item.model_version,
        signals=derive_signals(item),
        note=decision.note,
    )
    session.add(event)
    session.flush()

    candidate = observe_candidate(session, capability, item, decision, kind, moment)
    if candidate is not None:
        event.candidate_rule_id = candidate.id
        session.flush()

    logger.info("learning event %s recorded as %s", event.id, kind.value)
    return LearningOutcome(event=event, candidate_rule=candidate)


def observe_candidate(
    session: Session,
    capability: CapabilityConfig,
    item: ReviewItem,
    decision: ReviewDecision,
    kind: LearningKind,
    now: datetime,
) -> CandidateRule | None:
    """Note the rule a correction would imply, without adopting it."""
    if kind not in CORRECTIONS or decision.action is None:
        return None
    if not capability.learning.allow_rule_learning:
        return None

    match = signals_to_match(item)
    if match is None:
        return None

    params = dict(decision.action_params or {})
    try:
        return record_rule(
            session,
            capability,
            match,
            ActionKind(decision.action),
            rationale=(
                f"Observed after {capability.name} mail from these senders was "
                f"corrected to {describe(decision.action, params)}."
            ),
            state=RuleState.OBSERVED,
            source=LEARNED_SOURCE,
            params=params,
            confidence=0.0,
            now=now,
        )
    except RuleRefused as exc:
        logger.info("no candidate rule recorded for item %s: %s", item.id, exc)
        return None


def describe(action: str, params: JsonObject) -> str:
    """An action said with the part of it that varies, for a rule's rationale."""
    destination = params.get(DESTINATION_PARAM)
    if action == ActionKind.GMAIL_MOVE and isinstance(destination, str):
        return f"{action} into {destination}"
    return action


def derive_signals(item: ReviewItem) -> JsonObject:
    """The retained metadata a rule could later be written against.

    Subject and addresses only: no body, no attachment, nothing that is not
    already stored on the review item.
    """
    return {
        "subject": item.subject,
        "participants": read_participants(item),
        "participant_domains": read_domains(item),
        "recommendation_source": item.recommendation_source,
    }


def signals_to_match(item: ReviewItem) -> MatchRule | None:
    """The narrowest rule this correction could support: its sender domains."""
    domains = read_domains(item)
    if not domains:
        return None
    return MatchRule(participant_domains=domains)


def read_participants(item: ReviewItem) -> list[str]:
    return [value for value in (item.participants or []) if isinstance(value, str)]


def read_domains(item: ReviewItem) -> list[str]:
    domains: list[str] = []
    for address in read_participants(item):
        _, separator, domain = address.partition("@")
        if separator and domain and domain not in domains:
            domains.append(domain)
    return domains


def read_learning_events(
    session: Session,
    capability_key: str | None = None,
    kinds: Sequence[LearningKind] | None = None,
    limit: int = 100,
) -> list[LearningEvent]:
    query = select(LearningEvent).order_by(
        LearningEvent.created_at.desc(), LearningEvent.id.desc()
    )
    if capability_key is not None:
        query = query.where(LearningEvent.capability_key == capability_key)
    if kinds is not None:
        query = query.where(LearningEvent.kind.in_([kind.value for kind in kinds]))
    return list(session.execute(query.limit(limit)).scalars().all())

