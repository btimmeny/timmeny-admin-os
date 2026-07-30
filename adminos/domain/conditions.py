"""What a rule matches on, in fields Admin OS can actually answer for.

A condition tree is `all`, `any` and `none` over leaves that name a field, an
operator and a value. Three things about it are less obvious than the shape:

**The fields are a closed list, and every one of them is backed by something
retained.** A rule that names `body` would be a rule about text this service
has never stored — bodies are not kept, by decision, and Gmail's short preview
is offered under its own name, `snippet`, so nobody writes a rule believing it
searches the message. A field with no data behind it is worse than a missing
feature: it is a rule that quietly matches nothing, or nothing but the first
two hundred characters.

**A rule has to narrow.** "All Inbox email", "any task older than a day", "any
email containing 'a'" are all expressible and all refused. Being in the inbox
is what every item in a review has in common, so it cannot be what a rule is
about; ages bound nothing on their own; and a two-character substring is a
coincidence waiting to happen. At least one condition must genuinely cut the
set down, and in an `any` branch every alternative must, because a branch that
matches everything makes the whole rule match everything.

**Matching explains itself.** Evaluation returns which conditions matched and
which did not, in the field's own words, plus whatever a pattern captured. A
recommendation Brian cannot see the reason for is one he has to take on trust,
and the point of writing rules down is not having to.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Self, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from adminos.logging import get_logger


logger = get_logger(__name__)

MINIMUM_LITERAL = 4
"""How much fixed text a condition needs before it counts as narrowing.

Short enough to allow "KPMG", long enough that "a" and "the" cannot be what a
rule is about.
"""

MAXIMUM_DEPTH = 4
"""How deep a condition tree may nest, so a rule stays something readable."""

SECONDS_A_DAY = 86_400.0


class ConditionError(ValueError):
    """Raised when a condition cannot be read at all."""


class ConditionRefused(ValueError):
    """Raised when a condition is legible, and matches too much to be a rule."""


class FieldKind(StrEnum):
    TEXT = "text"
    TEXTS = "texts"
    NUMBER = "number"


class Operator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    REGEX = "regex"
    AT_LEAST = "at_least"
    AT_MOST = "at_most"


TEXT_OPERATORS = (
    Operator.EQUALS,
    Operator.NOT_EQUALS,
    Operator.CONTAINS,
    Operator.NOT_CONTAINS,
    Operator.STARTS_WITH,
    Operator.ENDS_WITH,
    Operator.REGEX,
)
NUMBER_OPERATORS = (Operator.EQUALS, Operator.AT_LEAST, Operator.AT_MOST)

NEGATIONS = {Operator.NOT_EQUALS, Operator.NOT_CONTAINS}

OPERATOR_PROSE = {
    Operator.EQUALS: "is",
    Operator.NOT_EQUALS: "is not",
    Operator.CONTAINS: "contains",
    Operator.NOT_CONTAINS: "does not contain",
    Operator.STARTS_WITH: "starts with",
    Operator.ENDS_WITH: "ends with",
    Operator.REGEX: "matches the pattern",
    Operator.AT_LEAST: "is at least",
    Operator.AT_MOST: "is at most",
}


@dataclass(frozen=True)
class FieldSpec:
    """One thing a rule may match on, and what stands behind it."""

    key: str
    label: str
    kind: FieldKind
    source: str
    narrowing: bool
    """Whether a condition on this field cuts the set down on its own.

    False for the facts every item in a review shares — being in the inbox,
    belonging to the group being reviewed — and for ages, which bound a rule
    to "eventually, everything".
    """

    def operators(self) -> tuple[Operator, ...]:
        return NUMBER_OPERATORS if self.kind is FieldKind.NUMBER else TEXT_OPERATORS


FIELDS: dict[str, FieldSpec] = {
    field.key: field
    for field in (
        FieldSpec(
            key="subject",
            label="the subject",
            kind=FieldKind.TEXT,
            source="The thread's subject, as Gmail gave it.",
            narrowing=True,
        ),
        FieldSpec(
            key="participant",
            label="an address on the thread",
            kind=FieldKind.TEXTS,
            source="Every address on the thread: senders and recipients alike.",
            narrowing=True,
        ),
        FieldSpec(
            key="participant_domain",
            label="a domain on the thread",
            kind=FieldKind.TEXTS,
            source="The domains of those addresses.",
            narrowing=True,
        ),
        FieldSpec(
            key="snippet",
            label="Gmail's preview text",
            kind=FieldKind.TEXT,
            source=(
                "Gmail's short preview of the newest message — roughly two "
                "hundred characters. Message bodies are not stored, so this is "
                "the whole of what a rule can read of what an email says."
            ),
            narrowing=True,
        ),
        FieldSpec(
            key="gmail_label",
            label="a Gmail label on the thread",
            kind=FieldKind.TEXTS,
            source="The labels Gmail had on the thread when it was last seen.",
            narrowing=False,
        ),
        FieldSpec(
            key="capability",
            label="the review group",
            kind=FieldKind.TEXT,
            source="The capability whose group the item was placed in.",
            narrowing=False,
        ),
        FieldSpec(
            key="thread_age_days",
            label="the thread's age in days",
            kind=FieldKind.NUMBER,
            source="Whole days since the newest message on the thread arrived.",
            narrowing=False,
        ),
    )
}


@dataclass(frozen=True)
class Facts:
    """One item, reduced to the fields conditions are written against.

    Built from a review item rather than read through it, so that evaluating a
    rule cannot reach for something the field list does not admit to.
    """

    text: Mapping[str, str | None]
    lists: Mapping[str, tuple[str, ...]]
    numbers: Mapping[str, float | None]

    def read_text(self, key: str) -> str | None:
        return self.text.get(key)

    def read_list(self, key: str) -> tuple[str, ...]:
        return self.lists.get(key, ())

    def read_number(self, key: str) -> float | None:
        return self.numbers.get(key)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(StrictModel):
    """One test: a field, an operator, and what to compare it against."""

    field: str
    operator: Operator
    value: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def check_field_and_operator(self) -> Self:
        spec = FIELDS.get(self.field)
        if spec is None:
            raise ValueError(
                f"{self.field!r} is not something a rule can match on. "
                f"The fields are: {', '.join(sorted(FIELDS))}."
            )
        if self.operator not in spec.operators():
            raise ValueError(
                f"{self.operator} does not apply to {spec.label} — "
                f"{', '.join(spec.operators())} do."
            )
        if spec.kind is FieldKind.NUMBER:
            try:
                float(self.value)
            except ValueError:
                raise ValueError(f"{self.value!r} is not a number.") from None
        if self.operator is Operator.REGEX:
            compile_pattern(self.value)
        return self

    def spec(self) -> FieldSpec:
        return FIELDS[self.field]

    def describes(self) -> str:
        """The condition in Brian's words rather than the engine's."""
        spec = self.spec()
        prose = OPERATOR_PROSE[self.operator]
        if self.operator is Operator.REGEX:
            return f"{spec.label} {prose} {readable_pattern(self.value)}"
        return f"{spec.label} {prose} {self.value!r}"

    def narrows(self) -> bool:
        """Whether this condition cuts the set down rather than describing it."""
        if not self.spec().narrowing or self.operator in NEGATIONS:
            return False
        if self.spec().kind is FieldKind.NUMBER:
            return False
        if self.operator is Operator.REGEX:
            return pattern_narrows(self.value)
        return len(self.value.strip()) >= MINIMUM_LITERAL

    def test(self, facts: Facts) -> Outcome:
        spec = self.spec()
        if spec.kind is FieldKind.NUMBER:
            return self.test_number(facts.read_number(self.field))
        if spec.kind is FieldKind.TEXTS:
            return self.test_texts(facts.read_list(self.field))
        return self.test_text(facts.read_text(self.field))

    def test_text(self, value: str | None) -> Outcome:
        if value is None:
            return Outcome(self, False, {}, f"{self.spec().label} is not set")
        matched, captured = compare(self.operator, value, self.value)
        return Outcome(self, matched, captured, self.saying(matched, value))

    def test_texts(self, values: Sequence[str]) -> Outcome:
        """Any one of them satisfying it satisfies the condition.

        Except for a negation, where all of them must: "no address from this
        domain" is not satisfied by there being some other address too.
        """
        if self.operator in NEGATIONS:
            matched = all(compare(self.operator, value, self.value)[0] for value in values)
            return Outcome(self, matched, {}, self.saying(matched, ", ".join(values)))
        for value in values:
            matched, captured = compare(self.operator, value, self.value)
            if matched:
                return Outcome(self, True, captured, self.saying(True, value))
        return Outcome(self, False, {}, self.saying(False, ", ".join(values) or "nothing"))

    def test_number(self, value: float | None) -> Outcome:
        if value is None:
            return Outcome(self, False, {}, f"{self.spec().label} is not known")
        wanted = float(self.value)
        matched = {
            Operator.EQUALS: value == wanted,
            Operator.AT_LEAST: value >= wanted,
            Operator.AT_MOST: value <= wanted,
        }[self.operator]
        return Outcome(self, matched, {}, self.saying(matched, f"{value:g}"))

    def saying(self, matched: bool, seen: str) -> str:
        verdict = "matched" if matched else "did not match"
        return f"{self.describes()} — {verdict} ({shorten(seen)})"


class ConditionGroup(StrictModel):
    """Conditions combined: all of them, any of them, none of them."""

    all: list[Test] = []
    any: list[Test] = []
    none: list[Test] = []

    @model_validator(mode="after")
    def check_not_empty(self) -> Self:
        if not (self.all or self.any or self.none):
            raise ValueError("A condition group with nothing in it matches everything.")
        if depth_of(self) > MAXIMUM_DEPTH:
            raise ValueError(
                f"This nests more than {MAXIMUM_DEPTH} deep, which is a rule "
                "nobody can read back to Brian."
            )
        return self

    def narrows(self) -> bool:
        if self.all and any(test.narrows() for test in self.all):
            return True
        return bool(self.any) and all(test.narrows() for test in self.any)

    def test(self, facts: Facts) -> Match:
        outcomes: list[Outcome] = []
        matched = True

        for test in self.all:
            result = test.test(facts)
            outcomes.extend(result.outcomes)
            matched = matched and result.matched

        if self.any:
            results = [test.test(facts) for test in self.any]
            for result in results:
                outcomes.extend(result.outcomes)
            matched = matched and any(result.matched for result in results)

        for test in self.none:
            result = test.test(facts)
            outcomes.extend(
                Outcome(
                    outcome.condition,
                    outcome.matched,
                    outcome.captured,
                    f"{outcome.saying} (this one must not match)",
                )
                for outcome in result.outcomes
            )
            matched = matched and not result.matched

        captured: dict[str, str] = {}
        for outcome in outcomes:
            if outcome.matched:
                captured.update(outcome.captured)
        return Match(matched=matched, outcomes=tuple(outcomes), captured=captured)

    def describes(self) -> list[str]:
        """The group as lines, in the order they are evaluated."""
        lines = [one_line(test) for test in self.all]
        if self.any:
            alternatives = " or ".join(one_line(test) for test in self.any)
            lines.append(f"either {alternatives}")
        lines.extend(f"it is not the case that {one_line(test)}" for test in self.none)
        return lines


Test = Condition | ConditionGroup

ConditionGroup.model_rebuild()


@dataclass(frozen=True)
class Outcome:
    """What one condition did, and what it saw."""

    condition: Condition
    matched: bool
    captured: Mapping[str, str]
    saying: str

    @property
    def outcomes(self) -> tuple[Outcome, ...]:
        return (self,)


@dataclass(frozen=True)
class Match:
    """What a whole tree did, condition by condition."""

    matched: bool
    outcomes: tuple[Outcome, ...]
    captured: Mapping[str, str]

    def reasons(self) -> list[str]:
        return [outcome.saying for outcome in self.outcomes]

    def matched_reasons(self) -> list[str]:
        return [outcome.saying for outcome in self.outcomes if outcome.matched]


def check_breadth(group: ConditionGroup) -> None:
    """Refuse a rule that does not cut the set down.

    The examples the requirement names — all Inbox email, any email containing
    "money", every Monday item, any task older than a day — are all legible
    conditions, and all of them are a rule about nothing in particular. The
    cost of allowing one is a recommendation on every row of a morning.
    """
    if not group.narrows():
        raise ConditionRefused(
            "This matches too much to be a rule: nothing in it narrows. Add a "
            f"condition on a subject, an address or a domain with at least "
            f"{MINIMUM_LITERAL} characters of fixed text — labels, groups and "
            "ages describe the whole review rather than a part of it. In an "
            "'any' branch, every alternative has to narrow, because one that "
            "matches everything makes the rule match everything."
        )


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a rule's pattern, refusing what would not work at match time."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ConditionError(f"{pattern!r} is not a usable pattern: {exc}.") from exc


def pattern_narrows(pattern: str) -> bool:
    """Whether a pattern is about something rather than about anything.

    A pattern matching the empty string matches every subject there is, and
    one made only of wildcards and digit classes is the same rule written at
    greater length. Fixed characters are what makes it a rule.
    """
    compiled = compile_pattern(pattern)
    if compiled.search("") is not None:
        return False
    return len(literal_text(pattern)) >= MINIMUM_LITERAL


def literal_text(pattern: str) -> str:
    """The fixed characters of a pattern, with syntax and classes removed."""
    without_groups = re.sub(r"\(\?P<\w+>[^)]*\)|\[[^\]]*\]|\\[dDwWsS]", "", pattern)
    return re.sub(r"[\\^$.|?*+()\[\]{}\d\s]", "", without_groups)


def readable_pattern(pattern: str) -> str:
    """A pattern with its captures shown as the values they stand for."""
    shown = re.sub(r"\(\?P<(\w+)>[^)]*\)", lambda found: f"[{found.group(1)}]", pattern)
    shown = re.sub(r"\\([^A-Za-z0-9])", lambda found: found.group(1), shown)
    return shown.strip("^$")


def compare(operator: Operator, value: str, wanted: str) -> tuple[bool, dict[str, str]]:
    """One text comparison, with whatever a pattern captured."""
    if operator is Operator.REGEX:
        found = compile_pattern(wanted).search(value)
        if found is None:
            return False, {}
        return True, {name: text for name, text in found.groupdict().items() if text}

    left, right = value.casefold(), wanted.casefold()
    matched = {
        Operator.EQUALS: left == right,
        Operator.NOT_EQUALS: left != right,
        Operator.CONTAINS: right in left,
        Operator.NOT_CONTAINS: right not in left,
        Operator.STARTS_WITH: left.startswith(right),
        Operator.ENDS_WITH: left.endswith(right),
    }[operator]
    return matched, {}


def depth_of(test: Test, level: int = 1) -> int:
    if isinstance(test, Condition):
        return level
    children = [*test.all, *test.any, *test.none]
    return max((depth_of(child, level + 1) for child in children), default=level)


def one_line(test: Test) -> str:
    if isinstance(test, Condition):
        return test.describes()
    return "(" + ", and ".join(test.describes()) + ")"


def shorten(value: str, limit: int = 80) -> str:
    """Trim what was seen, so an explanation stays a sentence."""
    collapsed = " ".join(value.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def describe_fields() -> list[FieldSpec]:
    """Every field a rule may use, for a screen that has to list them."""
    return sorted(FIELDS.values(), key=lambda field: field.key)


def email_facts(
    *,
    subject: str | None,
    participants: Sequence[str],
    labels: Sequence[str],
    snippet: str | None,
    capability_key: str | None,
    received_at: datetime | None,
    now: datetime,
) -> Facts:
    """One email thread as the fields a rule may ask about.

    Everything a condition can see arrives through this function, which is the
    point of it: a field nobody passes in here is a field no rule can invent.
    """
    addresses = [address for address in participants if address]
    age = None if received_at is None else (now - received_at).total_seconds() / SECONDS_A_DAY
    return Facts(
        text={
            "subject": subject,
            "snippet": snippet,
            "capability": capability_key,
        },
        lists={
            "participant": tuple(addresses),
            "participant_domain": tuple(domains_of(addresses)),
            "gmail_label": tuple(label for label in labels if label),
        },
        numbers={"thread_age_days": age},
    )


def domains_of(addresses: Sequence[str]) -> list[str]:
    """The domain of each address that has one, in the order they appeared."""
    found: list[str] = []
    for address in addresses:
        _, _, domain = address.rpartition("@")
        if domain and domain not in found:
            found.append(domain.strip("> ").casefold())
    return found
