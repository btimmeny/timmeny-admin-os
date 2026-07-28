from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.adapters.gmail import SOURCE_SYSTEM, GmailClient, GmailThread
from adminos.db.models import Evidence
from adminos.logging import get_logger


DEFAULT_SYNC_LIMIT = 50
MAX_SYNC_LIMIT = 200

logger = get_logger(__name__)


class IntakeLabelMissing(RuntimeError):
    """Raised when the configured Gmail intake label does not exist."""


@dataclass(frozen=True)
class EvidenceSyncResult:
    label: str
    scanned: int
    created: int
    updated: int
    unchanged: int


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
) -> EvidenceSyncResult:
    """Record every thread carrying the intake label as evidence.

    Read-only with respect to Gmail and Monday: nothing is labelled, archived,
    or turned into a task here. Classification decides that later.
    """
    label_id = await client.resolve_label_id(label)
    if label_id is None:
        raise IntakeLabelMissing(f"Gmail has no label named {label!r}.")

    thread_ids = await client.list_thread_ids(label_id, min(limit, MAX_SYNC_LIMIT))
    counts = {"created": 0, "updated": 0, "unchanged": 0}

    for thread_id in thread_ids:
        thread = await client.fetch_thread(thread_id)
        counts[record_gmail_thread(session, thread)] += 1

    logger.info(
        "gmail evidence sync: label=%s scanned=%d created=%d updated=%d unchanged=%d",
        label,
        len(thread_ids),
        counts["created"],
        counts["updated"],
        counts["unchanged"],
    )

    return EvidenceSyncResult(
        label=label,
        scanned=len(thread_ids),
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
    )
