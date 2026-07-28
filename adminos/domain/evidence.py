from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from adminos.adapters.gmail import (
    INBOX_LABEL_ID,
    SOURCE_SYSTEM,
    GmailClient,
    GmailThread,
)
from adminos.db.models import Classification, Evidence
from adminos.logging import get_logger


DEFAULT_SYNC_LIMIT = 50
MAX_SYNC_LIMIT = 200

logger = get_logger(__name__)


class IntakeLabelMissing(RuntimeError):
    """Raised when the configured Gmail intake label does not exist."""


class PruneScanTruncated(RuntimeError):
    """Raised when a prune is asked for but the scan did not see every thread."""


@dataclass(frozen=True)
class EvidenceSyncResult:
    label: str
    scanned: int
    created: int
    updated: int
    unchanged: int
    removed: int = 0


def prune_gmail_evidence(session: Session, live_thread_ids: Sequence[str]) -> int:
    """Delete evidence for Gmail threads no longer in scope. Returns the count.

    Only safe when `live_thread_ids` is the complete in-scope set: anything
    absent from it is treated as retired and removed.

    Classifications of the retired evidence go with it. A classification is a
    statement *about* a thread and means nothing once the thread is gone, and
    leaving the rows behind would make the foreign key reject the delete.
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


def record_gmail_thread(session: Session, thread: GmailThread) -> str:
    """Store a thread as evidence. Returns 'created', 'updated', or 'unchanged'.

    Identity is the thread, not the message, so a reply to an already-recorded
    conversation updates one row rather than creating a second piece of
    evidence for the same subject.
    """
    existing = session.execute(
        select(Evidence).where(
            Evidence.source_system == SOURCE_SYSTEM,
            Evidence.source_thread_id == thread.thread_id,
        )
    ).scalar_one_or_none()

    content_hash = thread.content_hash()

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
            )
        )
        return "created"

    if existing.content_hash == content_hash:
        return "unchanged"

    existing.source_message_id = thread.message_id
    existing.subject = thread.subject
    existing.participants = thread.participants
    existing.received_at = thread.received_at
    existing.snippet = thread.snippet
    existing.content_hash = content_hash
    return "updated"


async def sync_gmail_evidence(
    client: GmailClient,
    session: Session,
    label: str,
    limit: int = DEFAULT_SYNC_LIMIT,
    prune: bool = False,
) -> EvidenceSyncResult:
    """Record inbox threads carrying the intake label as evidence.

    Scoped to the intersection of INBOX and the intake label. A thread the owner
    has already archived is deliberately out of scope: archiving is how they say
    they are done with it, so re-reading it would resurrect settled mail.

    With `prune`, evidence for threads outside that set is deleted, which also
    retires evidence recorded before the scope narrowed. Pruning is refused when
    the scan filled the page limit, because a truncated listing cannot
    distinguish "archived" from "further down the page".

    Read-only with respect to Gmail and Monday: nothing is labelled, archived,
    or turned into a task here. Classification decides that later.
    """
    label_id = await client.resolve_label_id(label)
    if label_id is None:
        raise IntakeLabelMissing(f"Gmail has no label named {label!r}.")

    effective_limit = min(limit, MAX_SYNC_LIMIT)
    thread_ids = await client.list_thread_ids([INBOX_LABEL_ID, label_id], effective_limit)
    counts = {"created": 0, "updated": 0, "unchanged": 0}

    if prune and len(thread_ids) >= effective_limit:
        raise PruneScanTruncated(
            f"The scan returned {len(thread_ids)} threads, filling the limit, so the "
            "in-scope set may be incomplete. Raise the limit and retry."
        )

    for thread_id in thread_ids:
        thread = await client.fetch_thread(thread_id)
        counts[record_gmail_thread(session, thread)] += 1

    removed = prune_gmail_evidence(session, thread_ids) if prune else 0

    logger.info(
        "gmail evidence sync: label=%s scanned=%d created=%d updated=%d "
        "unchanged=%d removed=%d",
        label,
        len(thread_ids),
        counts["created"],
        counts["updated"],
        counts["unchanged"],
        removed,
    )

    return EvidenceSyncResult(
        label=label,
        scanned=len(thread_ids),
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        removed=removed,
    )
