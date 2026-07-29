from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from adminos.adapters.gmail import (
    SOURCE_SYSTEM,
    GmailClient,
    GmailNotFound,
    GmailThread,
)
from adminos.capabilities.config import CapabilityConfig
from adminos.db.models import Classification, Evidence
from adminos.domain.mailboxes import DEFAULT_SCOPE, ReviewScope, capability_scope
from adminos.logging import get_logger


DEFAULT_SYNC_LIMIT = 50
MAX_SYNC_LIMIT = 200

logger = get_logger(__name__)


class PruneScanTruncated(RuntimeError):
    """Raised when a prune is asked for but the scan did not see every thread."""


class PruneScopeRefused(RuntimeError):
    """Raised when a prune is asked for from a scan of somewhere else.

    Pruning treats everything it did not see as gone. A scan of the archive has
    not seen the inbox, so pruning after one would retire the whole review.
    """


@dataclass(frozen=True)
class EvidenceSyncResult:
    scope: ReviewScope
    labels: list[str]
    scanned: int
    created: int
    updated: int
    unchanged: int
    removed: int = 0
    warnings: list[str] = field(default_factory=list)


def prune_gmail_evidence(session: Session, live_thread_ids: Sequence[str]) -> int:
    """Delete evidence for Gmail threads no longer in scope. Returns the count.

    Only safe when `live_thread_ids` is the complete in-scope set: anything
    absent from it is treated as retired and removed.

    Classifications of the retired evidence go with it. A classification is a
    statement *about* a thread and means nothing once the thread is gone, and
    leaving the rows behind would make the foreign key reject the delete.
    Review items deliberately survive: they record what was decided, and a
    decision must remain auditable after the thread it concerned is archived.
    """
    condition = Evidence.source_system == SOURCE_SYSTEM
    retired = select(Evidence.id).where(
        condition
        if not live_thread_ids
        else and_(condition, Evidence.source_thread_id.not_in(live_thread_ids))
    )

    session.execute(
        delete(Classification).where(Classification.evidence_id.in_(retired.scalar_subquery()))
    )
    removed = session.execute(delete(Evidence).where(Evidence.id.in_(retired.scalar_subquery())))
    return removed.rowcount or 0


def record_gmail_thread(
    session: Session,
    thread: GmailThread,
    capability_keys: Sequence[str],
) -> str:
    """Store a thread as evidence. Returns 'created', 'updated', or 'unchanged'.

    Identity is the thread, not the message, so a reply to an already-recorded
    conversation updates one row rather than creating a second piece of
    evidence for the same subject. The capabilities whose labels the thread
    carries are recorded on it: that attribution, not a branch in code, is what
    puts a thread in one review group rather than another. The thread's Gmail
    labels are recorded with it, because they are what a review scope is
    checked against.
    """
    existing = session.execute(
        select(Evidence).where(
            Evidence.source_system == SOURCE_SYSTEM,
            Evidence.source_thread_id == thread.thread_id,
        )
    ).scalar_one_or_none()

    content_hash = thread.content_hash()
    keys = sorted(set(capability_keys))

    if existing is None:
        session.add(
            Evidence(
                source_system=SOURCE_SYSTEM,
                source_thread_id=thread.thread_id,
                source_message_id=thread.message_id,
                subject=thread.subject,
                participants=thread.participants,
                received_at=thread.received_at,
                snippet=thread.snippet,
                content_hash=content_hash,
                capability_keys=keys,
                label_ids=list(thread.label_ids),
            )
        )
        return "created"

    unchanged = existing.content_hash == content_hash and existing.capability_keys == keys
    existing.label_ids = list(thread.label_ids)
    if unchanged:
        return "unchanged"

    existing.source_message_id = thread.message_id
    existing.subject = thread.subject
    existing.participants = thread.participants
    existing.received_at = thread.received_at
    existing.snippet = thread.snippet
    existing.content_hash = content_hash
    existing.capability_keys = keys
    return "updated"


async def sync_gmail_evidence(
    client: GmailClient,
    session: Session,
    capabilities: Sequence[CapabilityConfig],
    scope: ReviewScope = DEFAULT_SCOPE,
    limit: int = DEFAULT_SYNC_LIMIT,
    prune: bool = False,
) -> EvidenceSyncResult:
    """Record the threads each capability watches, attributed to that capability.

    The scope is part of the query, not a filter applied afterwards by whoever
    reads the result. By default it is the intersection of INBOX and the
    capability's label, with snoozed threads excluded: a thread the owner has
    archived, trashed, or snoozed is out of scope because those are all ways of
    saying it is not today's business.

    What Gmail returns is then checked against the scope again, thread by
    thread, using the labels the thread actually carries. Search is Gmail's
    interpretation of a question; labels are the answer.

    A label that does not exist is reported as a warning rather than an error,
    so one mistyped label in configuration cannot stop the other capabilities
    from being reviewed. Pruning, which needs the complete in-scope set, is the
    exception: it is refused if any label failed to resolve.

    Read-only with respect to Gmail and Monday: nothing is labelled, archived,
    or turned into a task here.
    """
    if prune and scope.name != DEFAULT_SCOPE.name:
        raise PruneScopeRefused(
            f"This scan covers the {scope.name} scope, which is not the set pruning "
            "is allowed to retire evidence against."
        )

    effective_limit = min(limit, MAX_SYNC_LIMIT)
    attribution: dict[str, list[str]] = {}
    watched_by_capability: dict[str, ReviewScope] = {}
    label_owners: dict[str, list[str]] = {}
    labels: list[str] = []
    warnings: list[str] = []
    # Warnings that mean the scan did not see everything it should have. Only
    # these forbid a prune: a thread left out because it is out of scope was
    # seen and judged, which is the opposite of having been missed.
    incomplete: list[str] = []

    for capability in capabilities:
        watched = capability_scope(scope, capability)
        watched_by_capability[capability.key] = watched
        for label in capability.gmail.labels:
            labels.append(label)
            label_id = await client.resolve_label_id(label)
            if label_id is None:
                incomplete.append(
                    f"Gmail has no label named {label!r}, so {capability.key!r} was skipped."
                )
                continue

            label_owners.setdefault(label_id, []).append(capability.key)
            thread_ids = await client.list_thread_ids(
                watched.label_ids(label_id),
                effective_limit,
                query=watched.query(),
            )
            if len(thread_ids) >= effective_limit:
                incomplete.append(
                    f"The scan of {label!r} filled the limit of {effective_limit}, so "
                    "older threads with that label were not seen."
                )
            for thread_id in thread_ids:
                attribution.setdefault(thread_id, []).append(capability.key)

    check_prune_is_safe(prune, incomplete)

    counts = {"created": 0, "updated": 0, "unchanged": 0}
    recorded: list[str] = []
    out_of_scope = 0

    for thread_id, capability_keys in attribution.items():
        thread = await client.fetch_thread(thread_id)
        in_scope = [
            key for key in capability_keys if watched_by_capability[key].admits(thread.label_ids)
        ]
        if not in_scope:
            out_of_scope += 1
            note_thread_labels(session, thread)
            continue
        counts[record_gmail_thread(session, thread, in_scope)] += 1
        recorded.append(thread_id)

    if out_of_scope:
        warnings.append(
            f"{out_of_scope} threads Gmail returned were not in the {scope.name} scope "
            "once their labels were read, and were left out."
        )

    rechecked = 0
    for evidence in read_stale_gmail_threads(session, watched_by_capability):
        if evidence.source_thread_id in attribution:
            continue
        if rechecked >= effective_limit:
            incomplete.append(
                f"More than {effective_limit} recorded threads were missing from this "
                "scan, so some may still be listed as in scope after leaving it."
            )
            break

        rechecked += 1
        verdict = await recheck_thread(
            client,
            session,
            evidence,
            label_owners,
            watched_by_capability,
        )
        if verdict is None:
            continue
        counts[verdict] += 1
        recorded.append(evidence.source_thread_id)

    check_prune_is_safe(prune, incomplete)

    removed = prune_gmail_evidence(session, recorded) if prune else 0

    logger.info(
        "gmail evidence sync: scope=%s labels=%d scanned=%d created=%d updated=%d "
        "unchanged=%d removed=%d out_of_scope=%d warnings=%d",
        scope.name,
        len(labels),
        len(recorded),
        counts["created"],
        counts["updated"],
        counts["unchanged"],
        removed,
        out_of_scope,
        len(warnings) + len(incomplete),
    )

    return EvidenceSyncResult(
        scope=scope,
        labels=labels,
        scanned=len(recorded),
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        removed=removed,
        warnings=incomplete + warnings,
    )


def check_prune_is_safe(prune: bool, incomplete: Sequence[str]) -> None:
    """Refuse a prune whose scan missed mail, before anything is deleted."""
    if prune and incomplete:
        raise PruneScanTruncated(
            "Pruning needs the complete in-scope set, and this scan was incomplete: "
            + " ".join(incomplete)
        )


def read_stale_gmail_threads(
    session: Session,
    watched_by_capability: dict[str, ReviewScope],
) -> list[Evidence]:
    """Recorded threads whose whereabouts this scan cannot have confirmed.

    A scan of the inbox returns what is in the inbox, so it says nothing about
    a thread that has left it: those are the rows whose labels can be stale and
    have to be asked about directly.

    A thread already known to be out of scope is not one of them. Its labels
    already say it is not being reviewed, and if it returns to the inbox the
    scan will find it — so there is nothing to learn by asking Gmail again.
    """
    rows = (
        session.execute(select(Evidence).where(Evidence.source_system == SOURCE_SYSTEM))
        .scalars()
        .all()
    )
    stale: list[Evidence] = []
    for row in rows:
        watched = [
            watched_by_capability[key]
            for key in row.capability_keys or []
            if key in watched_by_capability
        ]
        if not watched:
            continue
        if row.label_ids is not None and not any(
            scope.admits(row.label_ids) for scope in watched
        ):
            continue
        stale.append(row)
    return stale


def note_thread_labels(session: Session, thread: GmailThread) -> None:
    """Record where a thread is now, without admitting it to any review."""
    existing = session.execute(
        select(Evidence).where(
            Evidence.source_system == SOURCE_SYSTEM,
            Evidence.source_thread_id == thread.thread_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.label_ids = list(thread.label_ids)


async def recheck_thread(
    client: GmailClient,
    session: Session,
    evidence: Evidence,
    label_owners: dict[str, list[str]],
    watched_by_capability: dict[str, ReviewScope],
) -> str | None:
    """Read where a recorded thread is now. Returns a count key if in scope.

    A scan of the inbox cannot report a thread that has left the inbox, so a
    thread already recorded and not returned is asked about directly. This is
    what makes archiving a thread take it out of the review rather than leaving
    it there with the labels it had the last time it was seen.

    A thread Gmail no longer has is recorded as being nowhere, which no scope
    admits.
    """
    try:
        thread = await client.fetch_thread(evidence.source_thread_id)
    except GmailNotFound:
        evidence.label_ids = []
        return None

    owners = [
        key
        for label_id, keys in label_owners.items()
        if label_id in thread.label_ids
        for key in keys
    ]
    in_scope = [key for key in owners if watched_by_capability[key].admits(thread.label_ids)]
    if not in_scope:
        evidence.label_ids = list(thread.label_ids)
        return None
    return record_gmail_thread(session, thread, in_scope)
