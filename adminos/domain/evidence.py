from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from adminos.adapters.gmail import (
    INBOX_LABEL_ID,
    SOURCE_SYSTEM,
    GmailClient,
    GmailThread,
)
from adminos.capabilities.config import CapabilityConfig
from adminos.db.models import Classification, Evidence
from adminos.logging import get_logger


DEFAULT_SYNC_LIMIT = 50
MAX_SYNC_LIMIT = 200

logger = get_logger(__name__)


class PruneScanTruncated(RuntimeError):
    """Raised when a prune is asked for but the scan did not see every thread."""


@dataclass(frozen=True)
class EvidenceSyncResult:
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
    puts a thread in one review group rather than another.
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
            )
        )
        return "created"

    if existing.content_hash == content_hash and existing.capability_keys == keys:
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
    limit: int = DEFAULT_SYNC_LIMIT,
    prune: bool = False,
) -> EvidenceSyncResult:
    """Record the threads each capability watches, attributed to that capability.

    Scoped to the intersection of INBOX and the capability's label. A thread the
    owner has already archived is deliberately out of scope: archiving is how
    they say they are done with it, so re-reading it would resurrect settled
    mail.

    A label that does not exist is reported as a warning rather than an error,
    so one mistyped label in configuration cannot stop the other capabilities
    from being reviewed. Pruning, which needs the complete in-scope set, is the
    exception: it is refused if any label failed to resolve.

    Read-only with respect to Gmail and Monday: nothing is labelled, archived,
    or turned into a task here.
    """
    effective_limit = min(limit, MAX_SYNC_LIMIT)
    attribution: dict[str, list[str]] = {}
    labels: list[str] = []
    warnings: list[str] = []

    for capability in capabilities:
        for label in capability.gmail.labels:
            labels.append(label)
            label_id = await client.resolve_label_id(label)
            if label_id is None:
                warnings.append(
                    f"Gmail has no label named {label!r}, so {capability.key!r} was skipped."
                )
                continue

            scope = [INBOX_LABEL_ID, label_id] if capability.gmail.require_inbox else [label_id]
            thread_ids = await client.list_thread_ids(scope, effective_limit)
            if len(thread_ids) >= effective_limit:
                warnings.append(
                    f"The scan of {label!r} filled the limit of {effective_limit}, so "
                    "older threads with that label were not seen."
                )
            for thread_id in thread_ids:
                attribution.setdefault(thread_id, []).append(capability.key)

    if prune and warnings:
        raise PruneScanTruncated(
            "Pruning needs the complete in-scope set, and this scan was incomplete: "
            + " ".join(warnings)
        )

    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for thread_id, capability_keys in attribution.items():
        thread = await client.fetch_thread(thread_id)
        counts[record_gmail_thread(session, thread, capability_keys)] += 1

    removed = prune_gmail_evidence(session, list(attribution)) if prune else 0

    logger.info(
        "gmail evidence sync: labels=%d scanned=%d created=%d updated=%d "
        "unchanged=%d removed=%d warnings=%d",
        len(labels),
        len(attribution),
        counts["created"],
        counts["updated"],
        counts["unchanged"],
        removed,
        len(warnings),
    )

    return EvidenceSyncResult(
        labels=labels,
        scanned=len(attribution),
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        removed=removed,
        warnings=warnings,
    )
