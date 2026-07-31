"""Where the review playbook lives, and how a review pins the version it ran under.

The same `playbook_revisions` table as the session playbook, under a different
`playbook_id`: a revision is a whole document written once, with the one it
replaced kept and readable. Two kinds of playbook, one way of versioning them,
because "which version was this decided under?" is the same question in both.

Pinning is the point. A review holds a revision id for its whole life, so a
group added at ten o'clock does not change what a review started at nine is
being held to, and a submission naming a different version is refused rather
than quietly accepted against the current one.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.config import get_review_playbook_path
from adminos.db.models import PlaybookRevision
from adminos.domain.playbook_store import RevisionNotFound, RevisionRefused, RevisionStatus
from adminos.domain.review_playbook import (
    DEFAULT_PLAYBOOK_ID,
    ConfigReport,
    ReviewPlaybookDocument,
    read_review_playbook,
    read_review_playbook_file,
    report_document,
    validate_review_playbook,
)
from adminos.logging import get_logger


logger = get_logger(__name__)

DEFAULT_REVIEW_PLAYBOOK_FILE = (
    Path(__file__).resolve().parents[2] / "config" / "review-playbook.yaml"
)

SEED_ACTOR = "admin-os"


@dataclass(frozen=True)
class ActiveReviewPlaybook:
    revision: PlaybookRevision
    document: ReviewPlaybookDocument
    report: ConfigReport


def review_playbook_file() -> Path:
    configured = get_review_playbook_path()
    return Path(configured) if configured else DEFAULT_REVIEW_PLAYBOOK_FILE


def read_active_review_playbook(
    session: Session,
    playbook_id: str = DEFAULT_PLAYBOOK_ID,
    now: datetime | None = None,
) -> ActiveReviewPlaybook:
    """The review playbook in force, seeded from the shipped file if there is none."""
    moment = now or datetime.now(UTC)
    active = read_status(session, playbook_id, RevisionStatus.ACTIVE)
    if active is None:
        active = seed_review_playbook(session, playbook_id, moment)

    document = read_review_playbook(dict(active.document))
    report = validate_review_playbook(document)
    if not report.valid:
        raise RevisionRefused(
            f"Review playbook revision {active.id} can no longer be run: "
            + "; ".join(error.message for error in report.errors)
        )
    return ActiveReviewPlaybook(revision=active, document=document, report=report)


def read_pinned_review_playbook(
    session: Session, revision_id: str
) -> ActiveReviewPlaybook:
    """The exact revision a review was started under, whatever is in force now."""
    revision = session.get(PlaybookRevision, revision_id)
    if revision is None:
        raise RevisionNotFound(f"No review playbook revision {revision_id!r} exists.")
    document = read_review_playbook(dict(revision.document))
    return ActiveReviewPlaybook(
        revision=revision,
        document=document,
        report=validate_review_playbook(document),
    )


def seed_review_playbook(
    session: Session, playbook_id: str, now: datetime
) -> PlaybookRevision:
    document = read_review_playbook_file(review_playbook_file())
    report = validate_review_playbook(document)
    if not report.valid:
        raise RevisionRefused(
            "The review playbook this service ships with does not validate: "
            + "; ".join(error.message for error in report.errors)
        )

    revision = PlaybookRevision(
        playbook_id=playbook_id,
        number=next_number(session, playbook_id),
        status=RevisionStatus.ACTIVE,
        document=document.model_dump(mode="json"),
        validation=report_document(report),
        change_summary=["The review playbook Admin OS starts from."],
        created_by=SEED_ACTOR,
        activated_at=now,
    )
    session.add(revision)
    session.flush()
    logger.info("review playbook %s seeded as revision %s", playbook_id, revision.id)
    return revision


def revise_review_playbook(
    session: Session,
    document: ReviewPlaybookDocument,
    summary: list[str],
    actor: str,
    playbook_id: str = DEFAULT_PLAYBOOK_ID,
    now: datetime | None = None,
) -> ActiveReviewPlaybook:
    """Put a new review playbook in force, leaving the old revision where it is.

    Reviews already under way keep the revision they pinned. That is what makes
    this safe to call: it changes what the next review is held to, not what a
    review being worked was agreed to be.
    """
    moment = now or datetime.now(UTC)
    report = validate_review_playbook(document)
    if not report.valid:
        raise RevisionRefused(
            "That review playbook cannot become the one in force: "
            + "; ".join(error.message for error in report.errors)
        )

    current = read_status(session, playbook_id, RevisionStatus.ACTIVE)
    if current is not None:
        current.status = RevisionStatus.SUPERSEDED
        current.superseded_at = moment

    revision = PlaybookRevision(
        playbook_id=playbook_id,
        number=next_number(session, playbook_id),
        status=RevisionStatus.ACTIVE,
        document=document.model_dump(mode="json"),
        validation=report_document(report),
        change_summary=summary,
        based_on_revision_id=current.id if current is not None else None,
        created_by=actor,
        activated_at=moment,
    )
    session.add(revision)
    session.flush()
    logger.info("review playbook %s is now revision %s", playbook_id, revision.id)
    return ActiveReviewPlaybook(revision=revision, document=document, report=report)


def read_status(
    session: Session, playbook_id: str, status: RevisionStatus
) -> PlaybookRevision | None:
    return (
        session.execute(
            select(PlaybookRevision)
            .where(
                PlaybookRevision.playbook_id == playbook_id,
                PlaybookRevision.status == status,
            )
            .order_by(PlaybookRevision.number.desc())
        )
        .scalars()
        .first()
    )


def next_number(session: Session, playbook_id: str) -> int:
    highest = (
        session.execute(
            select(PlaybookRevision.number)
            .where(PlaybookRevision.playbook_id == playbook_id)
            .order_by(PlaybookRevision.number.desc())
        )
        .scalars()
        .first()
    )
    return (highest or 0) + 1


__all__ = [
    "ActiveReviewPlaybook",
    "read_active_review_playbook",
    "read_pinned_review_playbook",
    "revise_review_playbook",
    "review_playbook_file",
    "seed_review_playbook",
]
