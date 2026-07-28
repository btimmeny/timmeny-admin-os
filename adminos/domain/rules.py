import hashlib
import json

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Sequence

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.capabilities.config import ActionKind, CapabilityConfig, MatchRule
from adminos.db.models import CandidateRule, JsonObject
from adminos.logging import get_logger


HUMAN_SOURCE = "human"
LEARNED_SOURCE = "learning"

logger = get_logger(__name__)


class RuleState(StrEnum):
    OBSERVED = "observed"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    AUTOMATABLE = "automatable"
    RETIRED = "retired"


ACTIVE_RULE_STATES = {RuleState.CONFIRMED, RuleState.AUTOMATABLE}
"""States in which a rule may shape a recommendation.

`observed` and `proposed` are deliberately inert: noticing a pattern, and
writing it down for review, must not change what the review says.
"""

TRANSITIONS: dict[RuleState, set[RuleState]] = {
    RuleState.OBSERVED: {RuleState.PROPOSED, RuleState.RETIRED},
    RuleState.PROPOSED: {RuleState.CONFIRMED, RuleState.RETIRED},
    RuleState.CONFIRMED: {RuleState.AUTOMATABLE, RuleState.RETIRED},
    RuleState.AUTOMATABLE: {RuleState.CONFIRMED, RuleState.RETIRED},
    RuleState.RETIRED: set(),
}
"""Every legal move. Confirming is not promoting, and retiring is final.

`automatable -> confirmed` exists so that permission to act unattended can be
withdrawn without discarding the rule itself.
"""


class RuleError(RuntimeError):
    """Raised when a rule operation is not valid."""


class RuleNotFound(RuleError):
    """Raised when a candidate rule does not exist."""


class RuleRefused(RuleError):
    """Raised when configuration forbids the rule or the transition asked for."""


@dataclass(frozen=True)
class LearnedRule:
    """An active candidate rule, in the form the review engine consumes."""

    id: str
    capability_key: str
    match: MatchRule
    action: ActionKind
    confidence: float
    rationale: str
    automatable: bool


def rule_digest(capability_key: str, match: MatchRule, action: ActionKind) -> str:
    """Identity of a rule by what it matches and does, not by who wrote it."""
    payload = json.dumps(
        {
            "capability": capability_key,
            "match": dump_match(match),
            "action": action.value,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def dump_match(match: MatchRule) -> JsonObject:
    """The exact conditions, with unstated ones omitted.

    What is stored is what is shown before confirmation: a reviewer must be
    able to read precisely what a rule will match.
    """
    return {
        key: value
        for key, value in match.model_dump(mode="json").items()
        if value not in (None, [], {})
    }


def load_match(conditions: JsonObject) -> MatchRule:
    try:
        return MatchRule.model_validate(conditions)
    except ValidationError as exc:
        raise RuleRefused(f"These match conditions are not usable: {exc}") from exc


def check_learning_allowed(capability: CapabilityConfig, action: ActionKind) -> None:
    if not capability.learning.allow_rule_learning:
        raise RuleRefused(f"{capability.key!r} does not learn rules.")
    if not capability.permits(action):
        raise RuleRefused(f"{capability.key!r} is not allowed to {action.value!r}.")


def record_rule(
    session: Session,
    capability: CapabilityConfig,
    match: MatchRule,
    action: ActionKind,
    rationale: str,
    state: RuleState,
    source: str,
    confidence: float = 0.0,
    actor: str | None = None,
    now: datetime | None = None,
) -> CandidateRule:
    """Create a rule in `state`, or return the one that already says this.

    Rules are identified by their conditions and action, so the same
    correction arriving twice raises the observation count of one candidate
    rather than filling the queue with duplicates.
    """
    check_learning_allowed(capability, action)
    if state not in {RuleState.OBSERVED, RuleState.PROPOSED}:
        raise RuleRefused(f"A rule cannot be created directly in {state.value!r}.")

    moment = now or datetime.now(UTC)
    conditions = dump_match(match)
    existing = find_equivalent(session, capability.key, match, action)

    if existing is not None:
        existing.observed_count += 1
        if existing.state == RuleState.OBSERVED and state is RuleState.PROPOSED:
            existing.state = RuleState.PROPOSED
            existing.proposed_by = actor
            existing.proposed_at = moment
            existing.rationale = rationale
        session.flush()
        return existing

    rule = CandidateRule(
        capability_key=capability.key,
        state=state,
        match_conditions=conditions,
        action=action.value,
        rationale=rationale,
        confidence=confidence,
        observed_count=1,
        source=source,
        policy_version=capability.recommendation_policy.version,
        proposed_by=actor if state is RuleState.PROPOSED else None,
        proposed_at=moment if state is RuleState.PROPOSED else None,
    )
    session.add(rule)
    session.flush()
    logger.info("candidate rule %s recorded as %s", rule.id, state.value)
    return rule


def find_equivalent(
    session: Session,
    capability_key: str,
    match: MatchRule,
    action: ActionKind,
) -> CandidateRule | None:
    wanted = rule_digest(capability_key, match, action)
    for rule in read_rules(session, capability_key=capability_key):
        if rule.state == RuleState.RETIRED:
            continue
        digest = rule_digest(
            rule.capability_key,
            load_match(rule.match_conditions),
            ActionKind(rule.action),
        )
        if digest == wanted:
            return rule
    return None


def transition_rule(
    session: Session,
    capability: CapabilityConfig,
    rule: CandidateRule,
    target: RuleState,
    actor: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> CandidateRule:
    """Move a rule one step, or refuse.

    Each step is a separate grant. Confirming makes a rule advise; only a
    further, explicit promotion lets it act without being asked first.
    """
    moment = now or datetime.now(UTC)
    current = RuleState(rule.state)
    if target not in TRANSITIONS[current]:
        raise RuleRefused(f"A {current.value!r} rule cannot become {target.value!r}.")

    action = ActionKind(rule.action)
    check_learning_allowed(capability, action)

    if target is RuleState.AUTOMATABLE:
        if not capability.learning.allow_automatable_rules:
            raise RuleRefused(
                f"{capability.key!r} does not allow rules to act without approval."
            )
        if not capability.may_execute(action):
            raise RuleRefused(
                f"{capability.key!r} may not execute {action.value!r}, so a rule that "
                "does it cannot run unattended."
            )
        rule.automatable_at = moment
    elif target is RuleState.CONFIRMED:
        rule.confirmed_by = actor
        rule.confirmed_at = moment
        rule.automatable_at = None
    elif target is RuleState.PROPOSED:
        rule.proposed_by = actor
        rule.proposed_at = moment
    elif target is RuleState.RETIRED:
        rule.retired_at = moment
        rule.retired_reason = reason

    rule.state = target
    session.flush()
    logger.info("candidate rule %s moved %s -> %s", rule.id, current.value, target.value)
    return rule


def read_rule(session: Session, rule_id: str) -> CandidateRule:
    rule = session.get(CandidateRule, rule_id)
    if rule is None:
        raise RuleNotFound(f"No candidate rule {rule_id!r}.")
    return rule


def read_rules(
    session: Session,
    capability_key: str | None = None,
    states: Sequence[RuleState] | None = None,
) -> list[CandidateRule]:
    query = select(CandidateRule).order_by(CandidateRule.created_at, CandidateRule.id)
    if capability_key is not None:
        query = query.where(CandidateRule.capability_key == capability_key)
    if states is not None:
        query = query.where(CandidateRule.state.in_([state.value for state in states]))
    return list(session.execute(query).scalars().all())


def read_active_rules(session: Session, capability: CapabilityConfig) -> list[LearnedRule]:
    """The rules that may currently shape this capability's recommendations.

    Learning is capability-scoped: a correction made about Admin mail never
    reaches the financial or career review.
    """
    if not capability.learning.allow_rule_learning:
        return []

    learned: list[LearnedRule] = []
    for rule in read_rules(session, capability.key, states=sorted(ACTIVE_RULE_STATES)):
        action = ActionKind(rule.action)
        if not capability.permits(action):
            logger.warning(
                "candidate rule %s recommends %s, which %s may no longer do",
                rule.id,
                action.value,
                capability.key,
            )
            continue
        automatable = (
            rule.state == RuleState.AUTOMATABLE
            and capability.learning.allow_automatable_rules
            and capability.may_execute(action)
        )
        learned.append(
            LearnedRule(
                id=rule.id,
                capability_key=rule.capability_key,
                match=load_match(rule.match_conditions),
                action=action,
                confidence=rule.confidence,
                rationale=rule.rationale,
                automatable=automatable,
            )
        )
    return learned
