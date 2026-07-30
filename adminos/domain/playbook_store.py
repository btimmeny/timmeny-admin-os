"""Where the playbook lives, and how it changes hands from one version to the next.

The file in `config/` is a seed: it is what an empty database starts from, and
after that the database is the playbook, because Brian changes it by asking
rather than by editing YAML. Every change writes a new revision and leaves the
old one where it was.

Nothing here activates anything on its own. A change is proposed, read back,
and confirmed, in that order, and the confirmation is a separate request.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.capabilities.config import LoadedCapabilities
from adminos.config import get_playbook_path
from adminos.db.models import PlaybookRevision
from adminos.domain.decisions import HUMAN_ACTOR
from adminos.domain.playbook import (
    DEFAULT_PLAYBOOK_ID,
    ChangeRefused,
    PlaybookChange,
    PlaybookDocument,
    ValidationMessage,
    ValidationReport,
    apply_changes,
    parse_playbook,
    read_playbook,
    validate_playbook,
)
from adminos.logging import get_logger


logger = get_logger(__name__)

DEFAULT_PLAYBOOK_FILE = (
    Path(__file__).resolve().parents[2] / "config" / "assistant-playbook.yaml"
)

SEED_ACTOR = "admin-os"
"""Who the first revision came from: nobody asked for it, it is the starting point."""


class RevisionStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


class RevisionNotFound(LookupError):
    """Raised when a revision is named that does not exist."""


class RevisionRefused(RuntimeError):
    """Raised when a revision cannot become active."""


@dataclass(frozen=True)
class ActivePlaybook:
    """The playbook in force, and what is true about it right now.

    `report` is recomputed rather than read from the revision: a playbook is
    valid against a set of capabilities, and capabilities change underneath it.
    A revision that named a capability which has since been removed stops being
    runnable without anybody having touched the playbook, and this is where
    that is noticed.
    """

    revision: PlaybookRevision
    document: PlaybookDocument
    report: ValidationReport
    fell_back_from: PlaybookRevision | None = None
    """The revision that was active and no longer validates, where one was."""


def read_active_playbook(
    session: Session,
    loaded: LoadedCapabilities,
    playbook_id: str = DEFAULT_PLAYBOOK_ID,
    now: datetime | None = None,
) -> ActivePlaybook:
    """The playbook a session should run, seeding and falling back as needed.

    Falling back matters more than it sounds. An active revision that stops
    validating cannot be run, and the choice is between refusing to hold a
    session at all and holding one on the last playbook that works. The second
    is better, provided it is said: the invalid revision is marked as such, and
    the session reports which playbook it is actually running.
    """
    moment = now or datetime.now(UTC)
    active = read_status(session, playbook_id, RevisionStatus.ACTIVE)

    if active is None:
        active = seed_playbook(session, loaded, playbook_id, moment)

    document = read_playbook(dict(active.document))
    report = validate_playbook(document, loaded)
    if report.valid:
        return ActivePlaybook(revision=active, document=document, report=report)

    logger.warning(
        "playbook revision %s is no longer valid: %s",
        active.id,
        "; ".join(error.message for error in report.errors),
    )
    active.status = RevisionStatus.INVALID
    active.validation = report_document(report)
    session.flush()

    fallback = last_valid(session, loaded, playbook_id)
    if fallback is None:
        raise RevisionRefused(
            f"No revision of {playbook_id!r} can be run against the configured "
            "capabilities: " + "; ".join(error.message for error in report.errors)
        )

    restored, restored_document, restored_report = fallback
    restored.status = RevisionStatus.ACTIVE
    restored.activated_at = moment
    restored.superseded_at = None
    session.flush()
    return ActivePlaybook(
        revision=restored,
        document=restored_document,
        report=restored_report,
        fell_back_from=active,
    )


def seed_playbook(
    session: Session,
    loaded: LoadedCapabilities,
    playbook_id: str,
    now: datetime,
) -> PlaybookRevision:
    """Write the first revision from the file this service ships with."""
    document = parse_playbook(playbook_file().read_bytes())
    report = validate_playbook(document, loaded)
    if not report.valid:
        raise RevisionRefused(
            "The playbook this service ships with does not validate: "
            + "; ".join(error.message for error in report.errors)
        )

    revision = PlaybookRevision(
        playbook_id=playbook_id,
        number=1,
        status=RevisionStatus.ACTIVE,
        document=document.model_dump(),
        validation=report_document(report),
        change_summary=["The playbook Admin OS starts from."],
        created_by=SEED_ACTOR,
        activated_at=now,
    )
    session.add(revision)
    session.flush()
    logger.info("playbook %s seeded as revision %s", playbook_id, revision.id)
    return revision


def playbook_file() -> Path:
    configured = get_playbook_path()
    return Path(configured) if configured else DEFAULT_PLAYBOOK_FILE


@dataclass(frozen=True)
class Proposal:
    """A playbook change written down but not in force.

    Returned so it can be read back before it is agreed to. The sentences are
    what Brian confirms; the document is what they do.
    """

    revision: PlaybookRevision
    document: PlaybookDocument
    report: ValidationReport
    summary: tuple[str, ...]


def propose_change(
    session: Session,
    loaded: LoadedCapabilities,
    changes: Sequence[PlaybookChange],
    rationale: str | None = None,
    playbook_id: str = DEFAULT_PLAYBOOK_ID,
    actor: str = HUMAN_ACTOR,
    now: datetime | None = None,
) -> Proposal:
    """Write what the playbook would become, without making it so.

    A proposal that would not validate is still written, as a proposal, with
    its errors: refusing to record it would leave Brian told "no" with nothing
    to look at, and it cannot be confirmed while it does not validate.
    """
    moment = now or datetime.now(UTC)
    active = read_active_playbook(session, loaded, playbook_id, moment)
    changed = apply_changes(active.document, changes, loaded)
    report = validate_playbook(changed.document, loaded)

    revision = PlaybookRevision(
        playbook_id=playbook_id,
        number=next_number(session, playbook_id),
        status=RevisionStatus.PROPOSED,
        document=changed.document.model_dump(),
        validation=report_document(report),
        change_summary=list(changed.summary),
        rationale=rationale,
        based_on_revision_id=active.revision.id,
        created_by=actor,
    )
    session.add(revision)
    session.flush()
    logger.info(
        "playbook %s revision %s proposed: %s",
        playbook_id,
        revision.id,
        "; ".join(changed.summary),
    )
    return Proposal(
        revision=revision, document=changed.document, report=report, summary=changed.summary
    )


def confirm_revision(
    session: Session,
    loaded: LoadedCapabilities,
    revision_id: str,
    now: datetime | None = None,
) -> ActivePlaybook:
    """Make a proposed revision the playbook, and set the old one aside.

    The revision is validated again here rather than trusted from when it was
    proposed. Between proposing and confirming, a capability can be removed,
    and a playbook that was runnable this morning may not be now. A proposal
    the playbook has moved on from is refused for the same reason: it was
    written against a revision that is no longer in force, so confirming it
    would quietly undo whatever was agreed to in between.
    """
    moment = now or datetime.now(UTC)
    revision = read_revision(session, revision_id)
    if revision.status == RevisionStatus.ACTIVE:
        raise RevisionRefused("That revision is already the playbook in force.")

    document = read_playbook(dict(revision.document))
    report = validate_playbook(document, loaded)
    revision.validation = report_document(report)
    if not report.valid:
        revision.status = RevisionStatus.INVALID
        session.flush()
        raise RevisionRefused(
            "That revision cannot become the playbook: "
            + "; ".join(error.message for error in report.errors)
        )

    current = read_status(session, revision.playbook_id, RevisionStatus.ACTIVE)
    if (
        revision.status == RevisionStatus.PROPOSED
        and current is not None
        and revision.based_on_revision_id is not None
        and revision.based_on_revision_id != current.id
    ):
        raise RevisionRefused(
            "The playbook has changed since that was proposed: it was written "
            f"against revision {revision.based_on_revision_id!r} and the playbook "
            f"is now {current.id!r}. Propose the change again so what is confirmed "
            "is what was read back."
        )

    if current is not None and current.id != revision.id:
        current.status = RevisionStatus.SUPERSEDED
        current.superseded_at = moment

    revision.status = RevisionStatus.ACTIVE
    revision.activated_at = moment
    revision.superseded_at = None
    session.flush()
    logger.info(
        "playbook %s is now revision %s, replacing %s",
        revision.playbook_id,
        revision.id,
        current.id if current is not None else "nothing",
    )
    return ActivePlaybook(revision=revision, document=document, report=report)


def read_revision(session: Session, revision_id: str) -> PlaybookRevision:
    revision = session.get(PlaybookRevision, revision_id)
    if revision is None:
        raise RevisionNotFound(f"No playbook revision {revision_id!r} exists.")
    return revision


def read_revisions(
    session: Session, playbook_id: str = DEFAULT_PLAYBOOK_ID
) -> list[PlaybookRevision]:
    return list(
        session.execute(
            select(PlaybookRevision)
            .where(PlaybookRevision.playbook_id == playbook_id)
            .order_by(PlaybookRevision.number.desc())
        )
        .scalars()
        .all()
    )


def read_status(
    session: Session, playbook_id: str, status: RevisionStatus
) -> PlaybookRevision | None:
    return session.execute(
        select(PlaybookRevision)
        .where(
            PlaybookRevision.playbook_id == playbook_id,
            PlaybookRevision.status == status,
        )
        .order_by(PlaybookRevision.number.desc())
    ).scalars().first()


def last_valid(
    session: Session, loaded: LoadedCapabilities, playbook_id: str
) -> tuple[PlaybookRevision, PlaybookDocument, ValidationReport] | None:
    """The newest revision that was in force once and still works."""
    for revision in read_revisions(session, playbook_id):
        if revision.status not in {RevisionStatus.SUPERSEDED, RevisionStatus.ACTIVE}:
            continue
        document = read_playbook(dict(revision.document))
        report = validate_playbook(document, loaded)
        if report.valid:
            return revision, document, report
    return None


def next_number(session: Session, playbook_id: str) -> int:
    revisions = read_revisions(session, playbook_id)
    return (revisions[0].number + 1) if revisions else 1


def report_document(report: ValidationReport) -> dict[str, object]:
    """The validation report as it is stored and returned."""
    return {
        "valid": report.valid,
        "errors": [message_document(message) for message in report.errors],
        "warnings": [message_document(message) for message in report.warnings],
    }


def message_document(message: ValidationMessage) -> dict[str, str]:
    return {"path": message.path, "code": message.code, "message": message.message}


__all__ = [
    "ActivePlaybook",
    "ChangeRefused",
    "Proposal",
    "RevisionNotFound",
    "RevisionRefused",
    "RevisionStatus",
    "confirm_revision",
    "propose_change",
    "read_active_playbook",
    "read_revision",
    "read_revisions",
    "report_document",
]
