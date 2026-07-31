"""What a rule would do, run against real mail and changing nothing.

A preview exists because the sentence a rule is written as and the effect it
has on a mailbox are different things, and only the second one matters at 7am.
So a test says how much of the sample it caught, which items, why each one,
and — the part that is easy to leave out — which items it nearly caught and
did not.

Nothing here writes to Gmail or Monday, and nothing here can: it reads
retained metadata, and the only thing it writes is the record that a test was
run. A matched item is never an approved item.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.capabilities.config import Mailbox
from adminos.db.models import Evidence
from adminos.domain.conditions import ConditionGroup, Facts, Match, email_facts
from adminos.domain.decisions import HUMAN_ACTOR
from adminos.domain.rulebook import Effect, RuleDraft, RuleStatus
from adminos.domain.rulebook_store import RuleRecord, move_rule, read_rule
from adminos.logging import get_logger


logger = get_logger(__name__)

DEFAULT_SAMPLE = 200
MAXIMUM_SAMPLE = 1000
HISTORY_DAYS = 90

BROAD_SHARE = 0.25
"""Matching more than a quarter of a sample is worth saying out loud.

Not an error: a group rule may well be that broad. But a rule written for one
kind of notice which catches a quarter of the mailbox has usually caught
something its author was not thinking about.
"""


class PreviewSource(StrEnum):
    """Which mail a rule is tried against."""

    CURRENT_SNAPSHOT = "current_snapshot"
    HISTORICAL_SAMPLE = "historical_sample"
    SELECTED_ITEMS = "selected_items"
    SYNTHETIC_EXAMPLES = "synthetic_examples"


@dataclass(frozen=True)
class Considered:
    """One item a rule was tried against, and what happened."""

    reference: str
    subject: str | None
    participants: tuple[str, ...]
    received_at: datetime | None
    groups: tuple[str, ...]
    match: Match

    def summary(self) -> str:
        """The item in one safe line: what it is, not what it says."""
        who = self.participants[0] if self.participants else "unknown sender"
        return f"{self.subject or '(no subject)'} — {who}"

    def reasons(self) -> tuple[str, ...]:
        return tuple(outcome.saying for outcome in self.match.outcomes)

    def near_misses(self) -> tuple[str, ...]:
        """The conditions that failed, where all but one passed.

        A rule that misses by one condition is the shape a false negative
        takes: the pattern is right and something about the wording is not.
        Failing the only condition there is, is not a near miss — it is a
        rule that is about other mail.
        """
        if self.match.matched or len(self.match.outcomes) < 2:
            return ()
        failed = [outcome for outcome in self.match.outcomes if not outcome.matched]
        if len(failed) != 1:
            return ()
        return (failed[0].saying,)


@dataclass(frozen=True)
class Preview:
    """What one matched item would be told, in the words it would be told in."""

    reference: str
    summary: str
    reasons: tuple[str, ...]
    captured: dict[str, str]
    effects: tuple[str, ...]
    groups: tuple[str, ...]
    requires_confirmation: bool = True
    """Always true. A rule that matched has recommended, and nothing more."""


@dataclass(frozen=True)
class Report:
    """A test run: what was tried, what matched, and what to be careful of."""

    test_run_id: str
    source: PreviewSource
    considered: int
    matched: tuple[Preview, ...]
    unmatched: tuple[str, ...]
    warnings: tuple[str, ...]
    false_positives: tuple[str, ...]
    false_negatives: tuple[str, ...]
    rule_id: str | None = None
    version_number: int | None = None
    executed: bool = False
    """Always false, and stated rather than assumed."""
    sampled_from: str = ""
    ran_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def share(self) -> float:
        if not self.considered:
            return 0.0
        return len(self.matched) / self.considered

    def counts(self) -> dict[str, int]:
        return {
            "considered": self.considered,
            "matched": len(self.matched),
            "unmatched": len(self.unmatched),
            "false_positive_candidates": len(self.false_positives),
            "false_negative_candidates": len(self.false_negatives),
        }


def preview_draft(
    session: Session,
    *,
    draft: RuleDraft,
    source: PreviewSource,
    thread_ids: Sequence[str] = (),
    subjects: Sequence[str] = (),
    limit: int = DEFAULT_SAMPLE,
    now: datetime | None = None,
) -> Report:
    """Run a rule against a sample of real mail, and change nothing.

    `synthetic_examples` matches subjects that were typed rather than
    received, which is how a rule can be tried on mail that has not arrived
    yet — and is reported as what it is, so nobody reads it as evidence about
    the mailbox.
    """
    moment = now or datetime.now(UTC)
    sample = _sample(
        session, source=source, thread_ids=thread_ids, subjects=subjects, limit=limit, now=moment
    )
    considered = [_consider(draft.match, item, moment) for item in sample]

    matched = tuple(
        _preview(draft, found) for found in considered if found.match.matched
    )
    unmatched = tuple(found.reference for found in considered if not found.match.matched)

    return Report(
        test_run_id=str(uuid4()),
        source=source,
        considered=len(considered),
        matched=matched,
        unmatched=unmatched,
        warnings=_warnings(draft, considered, source),
        false_positives=_false_positives(draft, considered),
        false_negatives=_false_negatives(draft, considered),
        sampled_from=_sampled_from(source, len(considered)),
        ran_at=moment,
    )


@dataclass(frozen=True)
class Item:
    """The metadata a rule may read, whatever it was read from."""

    reference: str
    subject: str | None
    participants: tuple[str, ...]
    labels: tuple[str, ...]
    snippet: str | None
    groups: tuple[str, ...]
    received_at: datetime | None


def _consider(match: ConditionGroup, item: Item, now: datetime) -> Considered:
    facts = email_facts(
        subject=item.subject,
        participants=item.participants,
        labels=item.labels,
        snippet=item.snippet,
        capability_key=item.groups[0] if item.groups else None,
        received_at=item.received_at,
        now=now,
    )
    return Considered(
        reference=item.reference,
        subject=item.subject,
        participants=item.participants,
        received_at=item.received_at,
        groups=item.groups,
        match=match.test(facts),
    )


def _preview(draft: RuleDraft, found: Considered) -> Preview:
    captured = dict(found.match.captured)
    return Preview(
        reference=found.reference,
        summary=found.summary(),
        reasons=found.reasons(),
        captured=captured,
        effects=tuple(render(effect, captured) for effect in draft.effects),
        groups=found.groups,
    )


def render(effect: Effect, captured: dict[str, str]) -> str:
    """What this effect would say, with the values it captured filled in."""
    said = effect.describes()
    for name, value in captured.items():
        said = said.replace("{{" + name + "}}", value)
    return said


def _warnings(
    draft: RuleDraft, considered: Sequence[Considered], source: PreviewSource
) -> tuple[str, ...]:
    matched = [found for found in considered if found.match.matched]
    warnings: list[str] = []

    if not considered:
        warnings.append(
            "There was nothing to try this against. A rule that has not been tried "
            "on anything has not been tested."
        )
    elif not matched:
        warnings.append(
            "This matched nothing in the sample. A rule that matches nothing looks "
            "safe and does nothing; check the wording against a real subject."
        )
    elif len(matched) / len(considered) > BROAD_SHARE:
        warnings.append(
            f"This matched {len(matched)} of {len(considered)} — most of the sample. "
            "A rule written for one kind of mail rarely catches that much."
        )

    if source is PreviewSource.SYNTHETIC_EXAMPLES:
        warnings.append(
            "These were examples typed out, not mail that arrived. They say the rule "
            "reads as intended; they say nothing about your mailbox."
        )

    if draft.positive_examples or draft.negative_examples:
        warnings.extend(_example_warnings(draft))

    return tuple(warnings)


def _example_warnings(draft: RuleDraft) -> list[str]:
    """Whether the rule does what its own examples say it should."""
    warnings: list[str] = []
    for subject in draft.positive_examples:
        if not draft.match.test(_typed(subject)).matched:
            warnings.append(f"It does not match its own example: {subject!r}.")
    for subject in draft.negative_examples:
        if draft.match.test(_typed(subject)).matched:
            warnings.append(f"It matches something it was told not to: {subject!r}.")
    return warnings


def _false_positives(draft: RuleDraft, considered: Sequence[Considered]) -> tuple[str, ...]:
    """Matches that look like they belong to somebody else's mail.

    A rule written for one group that matches mail reviewed under another is
    the commonest way a pattern turns out to be about the sender rather than
    about the notice.
    """
    if draft.capability_key is None:
        return ()
    strays = [
        f"{found.summary()} — reviewed as {', '.join(found.groups)}"
        for found in considered
        if found.match.matched and found.groups and draft.capability_key not in found.groups
    ]
    return tuple(strays)


def _false_negatives(draft: RuleDraft, considered: Sequence[Considered]) -> tuple[str, ...]:
    """Items this missed by one condition, which is how a miss usually looks."""
    return tuple(
        f"{found.summary()} — {found.near_misses()[0]}"
        for found in considered
        if found.near_misses()
    )


def _typed(subject: str) -> Facts:
    """Facts for a subject somebody wrote out, with nothing else claimed."""
    return email_facts(
        subject=subject,
        participants=(),
        labels=(),
        snippet=None,
        capability_key=None,
        received_at=None,
        now=datetime.now(UTC),
    )


def _sampled_from(source: PreviewSource, count: int) -> str:
    if source is PreviewSource.CURRENT_SNAPSHOT:
        return f"{count} threads in the inbox as it stands"
    if source is PreviewSource.HISTORICAL_SAMPLE:
        return f"{count} threads seen in the last {HISTORY_DAYS} days"
    if source is PreviewSource.SELECTED_ITEMS:
        return f"{count} threads you named"
    return f"{count} subjects you typed"


def _sample(
    session: Session,
    *,
    source: PreviewSource,
    thread_ids: Sequence[str],
    subjects: Sequence[str],
    limit: int,
    now: datetime,
) -> list[Item]:
    size = max(1, min(limit, MAXIMUM_SAMPLE))
    if source is PreviewSource.SYNTHETIC_EXAMPLES:
        return [_synthetic(subject) for subject in subjects[:size]]
    if source is PreviewSource.SELECTED_ITEMS:
        return _from_evidence(
            session.scalars(
                select(Evidence).where(Evidence.source_thread_id.in_(list(thread_ids)))
            ).all()
        )
    if source is PreviewSource.CURRENT_SNAPSHOT:
        return _from_evidence(
            session.scalars(
                select(Evidence)
                .where(Evidence.label_ids.is_not(None))
                .order_by(Evidence.received_at.desc())
                .limit(size)
            ).all(),
            inbox_only=True,
        )
    since = now - timedelta(days=HISTORY_DAYS)
    return _from_evidence(
        session.scalars(
            select(Evidence)
            .where(Evidence.received_at.is_not(None), Evidence.received_at >= since)
            .order_by(Evidence.received_at.desc())
            .limit(size)
        ).all()
    )


def _synthetic(subject: str) -> Item:
    return Item(
        reference=subject,
        subject=subject,
        participants=(),
        labels=(),
        snippet=None,
        groups=(),
        received_at=None,
    )


def _from_evidence(rows: Sequence[Evidence], inbox_only: bool = False) -> list[Item]:
    items: list[Item] = []
    for row in rows:
        labels = tuple(row.label_ids or ())
        if inbox_only and Mailbox.INBOX.value not in labels:
            continue
        items.append(
            Item(
                reference=row.source_thread_id,
                subject=row.subject,
                participants=tuple(row.participants or ()),
                labels=labels,
                snippet=row.snippet,
                groups=tuple(row.capability_keys or ()),
                received_at=_utc(row.received_at),
            )
        )
    return items


def _utc(moment: datetime | None) -> datetime | None:
    """Read a stored time as UTC, which is the only thing it was ever written as.

    SQLite hands back a naive datetime where Postgres hands back an aware one,
    and an age is arithmetic between the two.
    """
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def preview_rule(
    session: Session,
    *,
    rule_id: str,
    source: PreviewSource,
    thread_ids: Sequence[str] = (),
    subjects: Sequence[str] = (),
    limit: int = DEFAULT_SAMPLE,
    actor: str = HUMAN_ACTOR,
    now: datetime | None = None,
) -> tuple[RuleRecord, Report]:
    """Try a stored rule, and record that it was tried.

    Recording is the point at which a proposed rule becomes `tested`, which is
    the only way it can reach `confirmed`. A rule tested and then amended goes
    back to `proposed`, so the test that lets it be confirmed is always a test
    of the version being confirmed.
    """
    moment = now or datetime.now(UTC)
    record = read_rule(session, rule_id)
    report = preview_draft(
        session,
        draft=record.draft(),
        source=source,
        thread_ids=thread_ids,
        subjects=subjects,
        limit=limit,
        now=moment,
    )
    report = replace(
        report, rule_id=record.rule.id, version_number=record.version.number
    )

    if RuleStatus(record.rule.status) is RuleStatus.PROPOSED:
        record = move_rule(
            session,
            rule_id=rule_id,
            to=RuleStatus.TESTED,
            actor=actor,
            detail={
                "test_run_id": report.test_run_id,
                "source": report.source.value,
                "counts": report.counts(),
                "warnings": list(report.warnings),
            },
            now=moment,
        )
    logger.info(
        "rule tested",
        extra={
            "rule_id": rule_id,
            "test_run_id": report.test_run_id,
            "matched": len(report.matched),
            "considered": report.considered,
        },
    )
    return record, report
