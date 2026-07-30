"""Writing rules down, and moving them through their life a step at a time.

Nothing here activates anything. A rule is proposed, previewed, confirmed and
then activated, and each of those is a separate request made by a person. The
two writes worth understanding:

- **Amending writes a version and un-confirms the rule.** What Brian agreed to
  was a version. Change what it matches or what it does, and the standing goes
  back to `proposed` — otherwise an edit is a way to put a rule he never saw
  into force under the agreement he gave to a different one.
- **Retiring is final.** There is no path back, so the history of a rule never
  has to be read as "active, unless it was retired in between".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.capabilities.config import (
    CapabilityConfig,
    LoadedCapabilities,
    UnknownCapability,
)
from adminos.db.models import Rule, RuleEvent, RuleVersion
from adminos.domain.decisions import HUMAN_ACTOR
from adminos.domain.rulebook import (
    EFFECTIVE_STATUSES,
    TRANSITION_EVENTS,
    Constraints,
    Effect,
    EventKind,
    RuleDraft,
    RuleError,
    RuleStatus,
    RuleType,
    check_draft,
    check_transition,
)
from adminos.logging import get_logger


logger = get_logger(__name__)


class RuleNotFound(LookupError):
    """Raised when a rule is named that does not exist."""


@dataclass(frozen=True)
class RuleRecord:
    """A rule and the version in force, read together because neither is enough."""

    rule: Rule
    version: RuleVersion

    def draft(self) -> RuleDraft:
        """The version as the rule it states, read back through the model."""
        return RuleDraft(
            name=self.version.name,
            description=self.version.description or "",
            rule_type=RuleType(self.rule.rule_type),
            capability_key=self.rule.capability_key,
            priority=self.version.priority,
            match=self.version.match_conditions,
            effects=self.version.effects,
            constraints=Constraints.model_validate(self.version.constraints),
            positive_examples=list(_examples(self.version, "positive")),
            negative_examples=list(_examples(self.version, "negative")),
            change_reason=self.version.change_reason or "",
        )


def _examples(version: RuleVersion, side: str) -> Sequence[str]:
    stored = version.examples or {}
    found = stored.get(side)
    if not isinstance(found, list):
        return ()
    return tuple(str(example) for example in found)


def propose_rule(
    session: Session,
    *,
    draft: RuleDraft,
    loaded: LoadedCapabilities,
    actor: str = HUMAN_ACTOR,
    now: datetime | None = None,
) -> RuleRecord:
    """Write a rule down, as proposed and doing nothing.

    Refuses a rule that says the same thing as one already written, because
    two rules matching the same mail and recommending the same action is a
    conflict nobody meant to create.
    """
    moment = now or datetime.now(UTC)
    check_draft(draft, _capability(draft, loaded), _groups(loaded))

    digest = draft.digest()
    existing = session.scalars(
        select(Rule).where(Rule.digest == digest, Rule.status != RuleStatus.RETIRED)
    ).first()
    if existing is not None:
        raise RuleError(
            f"This is the rule {existing.id} already says, and it is {existing.status}. "
            "Amend that one rather than writing a second."
        )

    rule = Rule(
        rule_type=draft.rule_type.value,
        capability_key=draft.capability_key,
        status=RuleStatus.PROPOSED.value,
        digest=digest,
        created_by=actor,
    )
    session.add(rule)
    session.flush()

    version = _write_version(session, rule=rule, draft=draft, number=1, actor=actor, previous=None)
    rule.current_version_id = version.id
    _record(
        session,
        rule=rule,
        version=version,
        kind=EventKind.PROPOSED,
        actor=actor,
        to_status=RuleStatus.PROPOSED,
        detail={"summary": draft.describes()},
        now=moment,
    )
    session.flush()
    logger.info(
        "rule proposed", extra={"rule_id": rule.id, "rule_type": rule.rule_type, "actor": actor}
    )
    return RuleRecord(rule=rule, version=version)


def amend_rule(
    session: Session,
    *,
    rule_id: str,
    draft: RuleDraft,
    loaded: LoadedCapabilities,
    actor: str = HUMAN_ACTOR,
    now: datetime | None = None,
) -> RuleRecord:
    """Write the next version of a rule, and take away what the last one earned."""
    moment = now or datetime.now(UTC)
    record = read_rule(session, rule_id)
    rule = record.rule
    if rule.status == RuleStatus.RETIRED.value:
        raise RuleError("A retired rule stays retired. Write a new one.")
    if draft.rule_type.value != rule.rule_type:
        raise RuleError(
            f"This rule is a {rule.rule_type}. A {draft.rule_type} is a different rule, "
            "not another version of this one."
        )
    check_draft(draft, _capability(draft, loaded), _groups(loaded))

    previous = record.version
    version = _write_version(
        session,
        rule=rule,
        draft=draft,
        number=previous.number + 1,
        actor=actor,
        previous=previous,
    )
    was = RuleStatus(rule.status)
    rule.current_version_id = version.id
    rule.digest = draft.digest()
    rule.status = RuleStatus.PROPOSED.value
    rule.confirmed_at = None
    rule.activated_at = None
    rule.paused_at = None
    _record(
        session,
        rule=rule,
        version=version,
        kind=EventKind.AMENDED,
        actor=actor,
        from_status=was,
        to_status=RuleStatus.PROPOSED,
        detail={
            "number": version.number,
            "change_reason": draft.change_reason,
            "summary": draft.describes(),
            "stood_down_from": was.value if was in EFFECTIVE_STATUSES else None,
        },
        now=moment,
    )
    session.flush()
    logger.info(
        "rule amended",
        extra={"rule_id": rule.id, "version": version.number, "was": was.value, "actor": actor},
    )
    return RuleRecord(rule=rule, version=version)


def move_rule(
    session: Session,
    *,
    rule_id: str,
    to: RuleStatus,
    actor: str = HUMAN_ACTOR,
    reason: str | None = None,
    detail: dict[str, object] | None = None,
    now: datetime | None = None,
) -> RuleRecord:
    """Take a rule one step through its life, or refuse to."""
    moment = now or datetime.now(UTC)
    record = read_rule(session, rule_id)
    rule = record.rule
    was = RuleStatus(rule.status)
    check_transition(was, to)

    rule.status = to.value
    if to is RuleStatus.CONFIRMED:
        rule.confirmed_at = moment
    if to is RuleStatus.ACTIVE and rule.activated_at is None:
        rule.activated_at = moment
    rule.paused_at = moment if to is RuleStatus.PAUSED else None
    if to is RuleStatus.RETIRED:
        rule.retired_at = moment

    kind = TRANSITION_EVENTS[to]
    if to is RuleStatus.ACTIVE and was is RuleStatus.PAUSED:
        kind = EventKind.RESUMED
    _record(
        session,
        rule=rule,
        version=record.version,
        kind=kind,
        actor=actor,
        from_status=was,
        to_status=to,
        detail={"reason": reason, **(detail or {})},
        now=moment,
    )
    session.flush()
    logger.info(
        "rule moved",
        extra={"rule_id": rule.id, "from": was.value, "to": to.value, "actor": actor},
    )
    return RuleRecord(rule=rule, version=record.version)


def read_rule(session: Session, rule_id: str) -> RuleRecord:
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise RuleNotFound(f"No rule {rule_id!r}.")
    version = session.get(RuleVersion, rule.current_version_id) if rule.current_version_id else None
    if version is None:
        raise RuleNotFound(f"Rule {rule_id!r} has no version, which should not happen.")
    return RuleRecord(rule=rule, version=version)


def read_rules(
    session: Session,
    *,
    rule_type: RuleType | None = None,
    status: RuleStatus | None = None,
    capability_key: str | None = None,
) -> list[RuleRecord]:
    """Every rule matching the filters, most urgent priority first."""
    query = select(Rule)
    if rule_type is not None:
        query = query.where(Rule.rule_type == rule_type.value)
    if status is not None:
        query = query.where(Rule.status == status.value)
    if capability_key is not None:
        query = query.where(Rule.capability_key == capability_key)
    rules = session.scalars(query.order_by(Rule.created_at)).all()
    records = [read_rule(session, rule.id) for rule in rules]
    return sorted(records, key=lambda record: (record.version.priority, record.rule.created_at))


def read_effective_rules(
    session: Session, *, capability_key: str | None = None
) -> list[RuleRecord]:
    """The rules that shape a review right now, in the order they are consulted.

    Confirmed is not effective, and neither is paused. Priority orders them
    lowest number first, which is what a conflict is resolved by.
    """
    query = select(Rule).where(Rule.status.in_([status.value for status in EFFECTIVE_STATUSES]))
    if capability_key is not None:
        query = query.where(
            (Rule.capability_key == capability_key) | (Rule.capability_key.is_(None))
        )
    rules = session.scalars(query).all()
    records = [read_rule(session, rule.id) for rule in rules]
    return sorted(records, key=lambda record: (record.version.priority, record.rule.created_at))


def read_rule_versions(session: Session, rule_id: str) -> list[RuleVersion]:
    """Every version this rule has had, oldest first."""
    return list(
        session.scalars(
            select(RuleVersion).where(RuleVersion.rule_id == rule_id).order_by(RuleVersion.number)
        ).all()
    )


def read_rule_events(session: Session, rule_id: str) -> list[RuleEvent]:
    """Everything that happened to this rule, in order."""
    return list(
        session.scalars(
            select(RuleEvent).where(RuleEvent.rule_id == rule_id).order_by(RuleEvent.created_at)
        ).all()
    )


def _groups(loaded: LoadedCapabilities) -> tuple[str, ...]:
    return tuple(capability.key for capability in loaded.enabled())


def _capability(draft: RuleDraft, loaded: LoadedCapabilities) -> CapabilityConfig | None:
    if draft.capability_key is None:
        return None
    try:
        return loaded.get(draft.capability_key)
    except UnknownCapability:
        return None


def _write_version(
    session: Session,
    *,
    rule: Rule,
    draft: RuleDraft,
    number: int,
    actor: str,
    previous: RuleVersion | None,
) -> RuleVersion:
    version = RuleVersion(
        rule_id=rule.id,
        number=number,
        name=draft.name,
        description=draft.description or None,
        priority=draft.priority,
        match_conditions=draft.match.model_dump(mode="json"),
        effects=[effect.model_dump(mode="json") for effect in draft.effects],
        constraints=draft.constraints.model_dump(mode="json"),
        examples={"positive": draft.positive_examples, "negative": draft.negative_examples},
        summary=draft.describes(),
        digest=draft.digest(),
        change_reason=draft.change_reason or None,
        supersedes_version_id=previous.id if previous else None,
        created_by=actor,
    )
    session.add(version)
    session.flush()
    return version


def _record(
    session: Session,
    *,
    rule: Rule,
    version: RuleVersion | None,
    kind: EventKind,
    actor: str,
    now: datetime,
    from_status: RuleStatus | None = None,
    to_status: RuleStatus | None = None,
    detail: dict[str, object] | None = None,
) -> RuleEvent:
    event = RuleEvent(
        rule_id=rule.id,
        version_id=version.id if version else None,
        kind=kind.value,
        from_status=from_status.value if from_status else None,
        to_status=to_status.value if to_status else None,
        actor=actor,
        detail={key: value for key, value in (detail or {}).items() if value is not None} or None,
        created_at=now,
    )
    session.add(event)
    return event


def effects_of(record: RuleRecord) -> list[Effect]:
    """The version's effects, read back through the model that wrote them."""
    return [Effect.model_validate(effect) for effect in record.version.effects]
