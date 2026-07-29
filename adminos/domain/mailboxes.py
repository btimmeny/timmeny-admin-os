from dataclasses import dataclass, replace
from typing import Sequence

from adminos.adapters.gmail import (
    DRAFT_LABEL_ID,
    INBOX_LABEL_ID,
    SENT_LABEL_ID,
    SPAM_LABEL_ID,
    TRASH_LABEL_ID,
)
from adminos.capabilities.config import CapabilityConfig, Mailbox
from adminos.db.models import JsonObject


class UnknownScope(ValueError):
    """Raised when a review is asked for a mailbox scope that does not exist."""


@dataclass(frozen=True)
class ReviewScope:
    """Which mail a review looks at, decided before anything is read.

    Membership of the inbox is part of the query, not a preference: the default
    scope asks Gmail for threads that are in the inbox and not snoozed, and
    every thread is checked against the scope again once its labels are known.
    A review of anything else — archived, snoozed, everything — is a different
    scope, and only exists because it was asked for.
    """

    name: str
    mailbox: Mailbox
    include_snoozed: bool = False
    include_archived: bool = False
    include_trash: bool = False
    include_spam: bool = False
    include_sent: bool = False
    include_drafts: bool = False
    requested: bool = False
    """True when the caller named this scope, rather than getting the default."""

    def label_ids(self, label_id: str) -> list[str]:
        """The labels Gmail must AND together for this scope's search."""
        if self.mailbox is Mailbox.INBOX:
            return [INBOX_LABEL_ID, label_id]
        return [label_id]

    def query(self) -> str:
        """The Gmail search that narrows the scope beyond what labels can say.

        Snoozing is the reason this exists: Gmail's API publishes no SNOOZED
        label, so the only way to include or exclude snoozed threads is to ask
        for them in the search language.
        """
        terms: list[str] = []
        if self.mailbox is Mailbox.ARCHIVE:
            terms.append("-in:inbox")
        if self.mailbox is Mailbox.SNOOZED:
            terms.append("in:snoozed")
        elif not self.include_snoozed:
            terms.append("-in:snoozed")
        if self.include_trash or self.include_spam:
            terms.append("in:anywhere")
        return " ".join(terms)

    def observed_snoozed(self) -> bool | None:
        """What a scan of this scope proves about a thread it returned.

        Snoozing has no label, so a thread's snooze can only be learned from
        the search that found it: `in:snoozed` returns snoozed threads,
        `-in:snoozed` returns threads that are not, and a scan that asked
        neither proves nothing either way.
        """
        if self.mailbox is Mailbox.SNOOZED:
            return True
        if not self.include_snoozed:
            return False
        return None

    def admits(self, label_ids: Sequence[str] | None, snoozed: bool | None = None) -> bool:
        """Whether a thread last seen like this belongs in this review.

        The check Gmail's search cannot be trusted to have made, run against
        the labels the thread actually carries. A thread whose labels are
        unknown is not admitted: never having been seen in scope is not the
        same as being in it.

        `snoozed` is the one fact no label carries, so it is recorded when a
        scan observes it and read back here. A review of snoozed mail admits
        only threads a snoozed scan has seen — an unknown snooze is not a
        snooze — and every other review excludes threads known to be asleep.

        Carrying SENT or DRAFT is not disqualifying on its own — a conversation
        Brian has replied to, or started a reply to, is still in his inbox. It
        disqualifies a thread that is *only* sent mail or an unsent draft.
        """
        if label_ids is None:
            return False
        if self.mailbox is Mailbox.SNOOZED and snoozed is not True:
            return False
        if not self.include_snoozed and snoozed is True:
            return False

        labels = set(label_ids)
        if not self.include_trash and TRASH_LABEL_ID in labels:
            return False
        if not self.include_spam and SPAM_LABEL_ID in labels:
            return False

        in_inbox = INBOX_LABEL_ID in labels
        if self.mailbox is Mailbox.INBOX and not in_inbox:
            return False
        if self.mailbox is Mailbox.ARCHIVE and in_inbox:
            return False
        if not in_inbox:
            if not self.include_sent and SENT_LABEL_ID in labels:
                return False
            if not self.include_drafts and DRAFT_LABEL_ID in labels:
                return False
        return True

    def describes(self) -> str:
        """One sentence a reader can be shown about what was reviewed."""
        return DESCRIPTIONS[self.mailbox]

    def as_json(self) -> JsonObject:
        """The scope as it is stored on a run and reported in a response."""
        return {
            "name": self.name,
            "mailbox": self.mailbox.value,
            "include_snoozed": self.include_snoozed,
            "include_archived": self.include_archived,
            "include_trash": self.include_trash,
            "include_spam": self.include_spam,
            "include_sent": self.include_sent,
            "include_drafts": self.include_drafts,
            "requested": self.requested,
            "gmail_query": self.query(),
            "description": self.describes(),
        }


INBOX_SCOPE = ReviewScope(name="inbox", mailbox=Mailbox.INBOX)
ARCHIVED_SCOPE = ReviewScope(
    name="archived",
    mailbox=Mailbox.ARCHIVE,
    include_archived=True,
)
SNOOZED_SCOPE = ReviewScope(
    name="snoozed",
    mailbox=Mailbox.SNOOZED,
    include_snoozed=True,
)
EVERYTHING_SCOPE = ReviewScope(
    name="everything",
    mailbox=Mailbox.ANYWHERE,
    include_snoozed=True,
    include_archived=True,
    include_trash=True,
    include_spam=True,
    include_sent=True,
    include_drafts=True,
)

DEFAULT_SCOPE = INBOX_SCOPE
"""What a review looks at when nobody says otherwise: actionable inbox mail."""

SCOPES: dict[str, ReviewScope] = {
    scope.name: scope
    for scope in (INBOX_SCOPE, ARCHIVED_SCOPE, SNOOZED_SCOPE, EVERYTHING_SCOPE)
}

SCOPE_NAMES = sorted(SCOPES)

DESCRIPTIONS: dict[Mailbox, str] = {
    Mailbox.INBOX: (
        "Mail in the inbox now: archived, snoozed, trashed, spam, sent-only "
        "and draft-only threads were excluded."
    ),
    Mailbox.ARCHIVE: (
        "Mail carrying the capability's label that is no longer in the inbox. "
        "Trash and Spam were still excluded."
    ),
    Mailbox.SNOOZED: (
        "Mail Gmail is holding back until its snooze expires. Trash and Spam "
        "were still excluded."
    ),
    Mailbox.ANYWHERE: (
        "Everything Gmail holds for the capability's label, including Trash, "
        "Spam, snoozed, sent and drafts."
    ),
}


def read_scope(name: str | None) -> ReviewScope:
    """The scope a caller asked for, or the default when they asked for nothing.

    A scope that was named is marked as requested, which is what allows an
    explicit "review my archive" to override a capability's configured mailbox.
    """
    if name is None:
        return DEFAULT_SCOPE

    scope = SCOPES.get(name)
    if scope is None:
        raise UnknownScope(f"{name!r} is not a review scope. Choose one of: {SCOPE_NAMES}.")
    return replace(scope, requested=True)


def read_stored_scope(stored: object) -> ReviewScope:
    """The scope a run was started with, read back from what was stored."""
    if not isinstance(stored, dict):
        return DEFAULT_SCOPE

    name = stored.get("name")
    scope = SCOPES.get(name) if isinstance(name, str) else None
    if scope is None:
        return DEFAULT_SCOPE
    return replace(scope, requested=bool(stored.get("requested")))


def capability_scope(scope: ReviewScope, capability: CapabilityConfig) -> ReviewScope:
    """The scope one capability is reviewed under.

    A capability may be configured to watch a mailbox other than the inbox, and
    the default review then reads that capability from there. Any other scope
    is used as given: asking to see the archive means the archive, for every
    capability in the review.
    """
    if scope.name != DEFAULT_SCOPE.name:
        return scope
    if capability.gmail.mailbox is scope.mailbox:
        return scope
    return replace(scope_for(capability.gmail.mailbox), requested=scope.requested)


def scope_for(mailbox: Mailbox) -> ReviewScope:
    for scope in SCOPES.values():
        if scope.mailbox is mailbox:
            return scope
    raise UnknownScope(f"No review scope covers {mailbox!r}.")
