"""Turning one email Brian points at into a condition that is about a kind.

"Emails like this are financial" names an example, and the thing worth
learning from it is never the example. *Inter Institution Transfer Request
207960765 Will Occur in 3 Days* is one email; the rule is every email with
that shape, whatever the numbers are.

So this proposes several readings of a subject, from the narrowest up, and
says what each would and would not catch. It does not choose. A generated
pattern is a guess about which parts vary, and a guess that files mail is a
guess that needs looking at first — which is why every suggestion here is a
condition to preview, never a rule to run.
"""

from __future__ import annotations

import re

from dataclasses import dataclass

from adminos.domain.conditions import (
    MINIMUM_LITERAL,
    Condition,
    Operator,
    pattern_narrows,
    readable_pattern,
)
from adminos.logging import get_logger


logger = get_logger(__name__)

DIGITS = re.compile(r"\d+")

NUMBER_CAPTURE = "number"

MAXIMUM_SUBJECT = 300
"""Longer than this and a subject is not a subject; it is a paragraph."""


@dataclass(frozen=True)
class Suggestion:
    """One reading of the example, and what it costs.

    `breadth` orders them: 1 is the reading that catches almost nothing but
    the example itself, and each step up catches more of what Brian meant and
    more of what he did not.
    """

    key: str
    label: str
    condition: Condition
    summary: str
    breadth: int
    catches: str
    misses: str


def suggest_subject_conditions(subject: str) -> list[Suggestion]:
    """Readings of a subject line, narrowest first.

    The default — the first one — is the pattern with the varying numbers
    taken out where there are numbers to take out, and the exact subject where
    there are none. Both are narrow; neither is safe unpreviewed.
    """
    text = " ".join(subject.split())
    if not text:
        raise ValueError("An empty subject says nothing to make a rule from.")
    if len(text) > MAXIMUM_SUBJECT:
        text = text[:MAXIMUM_SUBJECT]

    found: list[Suggestion] = []
    pattern = numbers_as_captures(text)
    if pattern is not None and pattern_narrows(pattern):
        found.append(
            Suggestion(
                key="pattern",
                label="Same wording, any numbers",
                condition=Condition(field="subject", operator=Operator.REGEX, value=pattern),
                summary=f"The subject reads {readable_pattern(pattern)}",
                breadth=2,
                catches="Every email whose subject has this exact wording, whatever the numbers.",
                misses="A subject where any of the fixed words differ, including in punctuation.",
            )
        )

    found.append(
        Suggestion(
            key="exact",
            label="This exact subject",
            condition=Condition(field="subject", operator=Operator.EQUALS, value=text),
            summary=f"The subject is exactly {text!r}",
            breadth=1,
            catches="Emails whose subject is this and nothing else.",
            misses=(
                "The next one of these, if any part of it differs — which for a "
                "numbered notification is all of them."
            ),
        )
    )

    opening = fixed_opening(text)
    if opening is not None:
        found.append(
            Suggestion(
                key="starts_with",
                label="Subjects that begin this way",
                condition=Condition(
                    field="subject", operator=Operator.STARTS_WITH, value=opening
                ),
                summary=f"The subject starts with {opening!r}",
                breadth=3,
                catches="Anything opening with these words, however it goes on.",
                misses="A subject that says the same thing with something in front of it.",
            )
        )

    phrase = longest_fixed_phrase(text)
    if phrase is not None:
        found.append(
            Suggestion(
                key="contains",
                label="Subjects mentioning this phrase",
                condition=Condition(field="subject", operator=Operator.CONTAINS, value=phrase),
                summary=f"The subject contains {phrase!r}",
                breadth=4,
                catches="Anything with this phrase anywhere in the subject, including replies.",
                misses="Nothing much — this is the widest of these, and the easiest to surprise.",
            )
        )

    return sorted(found, key=lambda suggestion: suggestion.breadth)


def numbers_as_captures(subject: str) -> str | None:
    """The subject as a pattern with each run of digits captured by name.

    Returns None when there are no numbers: a pattern identical to the exact
    subject is the exact subject, said less clearly.
    """
    runs = list(DIGITS.finditer(subject))
    if not runs:
        return None

    pieces: list[str] = []
    cursor = 0
    for index, run in enumerate(runs, start=1):
        pieces.append(re.escape(subject[cursor : run.start()]))
        name = NUMBER_CAPTURE if len(runs) == 1 else f"{NUMBER_CAPTURE}_{index}"
        pieces.append(rf"(?P<{name}>\d+)")
        cursor = run.end()
    pieces.append(re.escape(subject[cursor:]))
    return "^" + "".join(pieces) + "$"


def fixed_opening(subject: str) -> str | None:
    """The words before the first number, if that is enough to be a condition."""
    first = DIGITS.search(subject)
    if first is None:
        return None
    opening = subject[: first.start()].strip()
    return opening if len(opening) >= MINIMUM_LITERAL else None


def longest_fixed_phrase(subject: str) -> str | None:
    """The longest stretch of the subject with no digits in it."""
    pieces = [piece.strip() for piece in DIGITS.split(subject)]
    longest = max(pieces, key=len, default="")
    return longest if len(longest) >= MINIMUM_LITERAL else None
