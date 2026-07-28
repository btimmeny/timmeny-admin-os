import math
import re

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Mapping, Sequence

from adminos.adapters.monday import MondayItem


# Tuned against the live board, where near-duplicates such as
# "GS | KO Shares (obtain cost basis)" and
# "GS | KO Shares (decide strategy and get cost basis)" already coexist.
STRONG_MATCH_SCORE = 0.75
CANDIDATE_SCORE = 0.45
DEFAULT_MATCH_LIMIT = 5
# A title wholly contained in a longer one is a strong candidate but not the
# same title, so it stops short of the score that means "this already exists".
SUBSET_MATCH_SCORE = 0.95

# Mail subjects arrive wrapped in reply and forward markers that say nothing
# about the underlying obligation.
SUBJECT_PREFIX = re.compile(r"^\s*((re|fwd|fw|aw|tr)\s*(\[\d+\])?\s*:\s*)+", re.IGNORECASE)
NON_WORD = re.compile(r"[^a-z0-9\s]+")
WHITESPACE = re.compile(r"\s+")

# Words that carry no distinguishing signal on a personal to-do board. "Tax"
# is deliberately absent: on this board it is a real discriminator.
NOISE_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "to",
        "of",
        "for",
        "from",
        "in",
        "on",
        "at",
        "by",
        "with",
        "into",
        "about",
        "or",
        "as",
        "is",
        "it",
        "me",
        "you",
        "this",
        "that",
        "your",
        "my",
        "our",
        "please",
        "update",
        "updates",
        "info",
        "information",
        "request",
        "regarding",
        "notification",
        "reminder",
    }
)


@dataclass(frozen=True)
class DuplicateMatch:
    item_id: str
    name: str
    status: str | None
    group: str | None
    admin_os_id: str | None
    score: float
    is_done: bool

    @property
    def is_strong(self) -> bool:
        return self.score >= STRONG_MATCH_SCORE


@dataclass(frozen=True)
class DuplicateReport:
    title: str
    normalized_title: str
    compared: int
    matches: list[DuplicateMatch]

    @property
    def has_strong_match(self) -> bool:
        return any(match.is_strong for match in self.matches)


def normalize_title(title: str) -> str:
    """Strip reply markers, punctuation, and case from a title or subject."""
    without_prefix = SUBJECT_PREFIX.sub("", title)
    lowered = NON_WORD.sub(" ", without_prefix.casefold())
    return WHITESPACE.sub(" ", lowered).strip()


def tokenize(title: str) -> set[str]:
    return {word for word in normalize_title(title).split() if word not in NOISE_WORDS}


@dataclass(frozen=True)
class TokenWeights:
    """How much each word counts as evidence that two titles match."""

    weights: Mapping[str, float]
    unseen: float

    def weigh(self, tokens: set[str]) -> float:
        # A word absent from the board is as distinctive as a word can be, so it
        # takes the maximum weight. Defaulting it low would let a title be
        # "contained" in another by its one shared common word.
        return sum(self.weights.get(token, self.unseen) for token in tokens)


UNIFORM_WEIGHTS = TokenWeights(weights={}, unseen=1.0)


def build_token_weights(titles: Sequence[str]) -> TokenWeights:
    """Weight each token by how rare it is on the board.

    Without this, "GS |" — which prefixes a large share of this board — counts
    as much evidence of duplication as "KPMG". Inverse document frequency makes
    a shared rare token worth more than several shared common ones, which is
    what a human comparing two task names actually does.
    """
    document_count = len(titles)
    frequencies: Counter[str] = Counter()
    for title in titles:
        frequencies.update(tokenize(title))
    return TokenWeights(
        weights={
            token: math.log(1 + document_count / (1 + frequency))
            for token, frequency in frequencies.items()
        },
        unseen=math.log(1 + document_count),
    )


def score_similarity(left: str, right: str, weights: TokenWeights = UNIFORM_WEIGHTS) -> float:
    """Return 0.0–1.0 for how likely two titles describe the same work.

    Two measures, because each fails differently. Weighted token overlap
    catches reordering and the board's "Context | Action" convention, where the
    same obligation appears as "Annual Taxes | KPMG" or "KPMG | Annual Taxes",
    but it ignores word order entirely. Sequence ratio catches near-identical
    phrasing and typos but is fooled by reordering. The higher of the two wins,
    since this decides whether a human is asked to look, not whether a task is
    created.

    Overlap is scored against the lighter of the two titles rather than their
    union: a short title wholly contained in a longer one is a duplicate, and
    penalising it for the extra words would hide exactly the case where someone
    re-adds an existing task in fewer words. Containment alone stops below 1.0
    though — "KPMG | Next Steps" is contained in "Taxes | Confirm KPMG
    activities and next steps" without being the same task, and a full score
    should mean the words match, not merely fit inside.
    """
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0

    shared_tokens = left_tokens & right_tokens
    if not shared_tokens:
        # Character similarity with no word in common is coincidence: it is what
        # scores "Call plumber about kitchen leak" against "Zac | Pimple
        # Patches" on shared letters alone.
        return 0.0

    shared = weights.weigh(shared_tokens)
    smaller = min(weights.weigh(left_tokens), weights.weigh(right_tokens))
    containment = shared / smaller if smaller else 0.0
    if left_tokens != right_tokens:
        containment = min(containment, SUBSET_MATCH_SCORE)
    sequence = SequenceMatcher(None, normalize_title(left), normalize_title(right)).ratio()
    return round(max(containment, sequence), 3)


def find_duplicates(
    title: str,
    items: Sequence[MondayItem],
    limit: int = DEFAULT_MATCH_LIMIT,
    threshold: float = CANDIDATE_SCORE,
) -> DuplicateReport:
    """Rank existing board items by how likely they already cover `title`.

    Completed items are included on purpose. Half this board is recurring
    obligation — annual filings, seasonal maintenance — so the useful answer to
    "is this a duplicate?" is often "yes, and you finished it last year".
    """
    weights = build_token_weights([item.name for item in items])
    scored = [
        DuplicateMatch(
            item_id=item.item_id,
            name=item.name,
            status=item.status,
            group=item.group,
            admin_os_id=item.admin_os_id,
            score=score_similarity(title, item.name, weights),
            is_done=item.is_done,
        )
        for item in items
    ]
    matches = sorted(
        (match for match in scored if match.score >= threshold),
        key=lambda match: (-match.score, match.is_done, match.name),
    )

    return DuplicateReport(
        title=title,
        normalized_title=normalize_title(title),
        compared=len(items),
        matches=matches[:limit],
    )
