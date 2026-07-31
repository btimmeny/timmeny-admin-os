"""A review Admin OS owns the process of and ChatGPT does the reading for.

The division is the whole point of this module. ChatGPT has the Gmail app, so
it reads the Inbox; Admin OS has the process, so it says what the groups are,
what every reviewed thread must state, and when a phase is finished — and then
holds the submission to exactly that. Neither half is trusted with the other's
job: Admin OS never claims to have read the mailbox, and ChatGPT never decides
what a complete review looks like.

Everything here refuses rather than repairs. A submission missing a required
field, naming a group the pinned playbook does not have, counting threads it
did not send, or leaving one item out of the recommended order is rejected
whole, with every fault named. Half a recorded review is worse than none: it
reads like a review of the mailbox and is a review of part of it.

Nothing here touches Gmail. Dispositions are recorded as recommendations, and
moving mail remains the existing, separately confirmed and verified path.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adminos.db.models import (
    GuidedReview,
    GuidedReviewEvent,
    GuidedReviewItem,
    GuidedReviewPhase,
    GuidedReviewSnapshot,
)
from adminos.domain.review_playbook import (
    EMAIL_REVIEW,
    Disposition,
    ItemField,
    MailboxScope,
    PhaseConfig,
    Urgency,
    known_phase,
)
from adminos.domain.review_playbook_store import (
    ActiveReviewPlaybook,
    read_active_review_playbook,
    read_pinned_review_playbook,
)
from adminos.logging import get_logger


logger = get_logger(__name__)

ASSISTANT_ACTOR = "chatgpt"
"""Who submits a review: the reasoning client, acting for Brian and named as it."""

LOW_CONFIDENCE = 0.5
"""Below this a row is worth Brian's eye before it is acted on."""

MAILBOX_CHANGING = frozenset(
    {Disposition.ARCHIVE, Disposition.MOVE_TO_TRASH, Disposition.FILE_TO_EXISTING_LABEL}
)


class ReviewStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    PARTIALLY_COMPLETE = "partially_complete"
    """Every phase that exists is done, and the rest are not built yet.

    Kept apart from `completed` on purpose: a review that has finished the one
    phase Admin OS implements has not been through the process Brian works.
    """

    COMPLETED = "completed"
    SUPERSEDED = "superseded"


class PhaseStatus(StrEnum):
    READY = "ready"
    RECORDED = "recorded"
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"


class EventKind(StrEnum):
    REVIEW_STARTED = "review_started"
    REVIEW_SUPERSEDED = "review_superseded"
    PLAYBOOK_LOADED = "playbook_loaded"
    PHASE_STARTED = "phase_started"
    SOURCE_SNAPSHOT_RECORDED = "source_snapshot_recorded"
    EMAIL_REVIEW_RECORDED = "email_review_recorded"
    VALIDATION_FAILED = "validation_failed"
    PHASE_COMPLETED = "phase_completed"


class ReviewNotFound(LookupError):
    """Raised when a review is named that does not exist."""


class ReviewRefused(RuntimeError):
    """Raised when an operation cannot be performed on a review as it stands."""


class RefusalCode(StrEnum):
    REVIEW_NOT_ACTIVE = "REVIEW_NOT_ACTIVE"
    WRONG_PHASE = "WRONG_PHASE"
    STALE_PLAYBOOK_VERSION = "STALE_PLAYBOOK_VERSION"
    WRONG_SCOPE = "WRONG_SCOPE"
    UNKNOWN_GROUP = "UNKNOWN_GROUP"
    MISSING_FIELD = "MISSING_FIELD"
    DUPLICATE_THREAD = "DUPLICATE_THREAD"
    COUNT_MISMATCH = "COUNT_MISMATCH"
    ORDER_INCOMPLETE = "ORDER_INCOMPLETE"
    ORDER_UNKNOWN_ITEM = "ORDER_UNKNOWN_ITEM"
    ORDER_DUPLICATE = "ORDER_DUPLICATE"
    NO_CATCH_ALL = "NO_CATCH_ALL"


@dataclass(frozen=True)
class Refusal:
    code: RefusalCode
    path: str
    message: str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceSnapshot(StrictModel):
    """What was read, from where, and when — as the reader reports it.

    `thread_count` is the reader's own count of what it found, checked against
    what it sent. Two numbers that should agree are how a review that quietly
    dropped a thread announces itself.
    """

    source: Literal["gmail"] = "gmail"
    mailbox_scope: MailboxScope
    observed_at: datetime
    thread_count: int = Field(ge=0)


class ReviewedItem(StrictModel):
    """One Inbox thread as ChatGPT reads it.

    Only the identity is required by the model. Everything else is required by
    the playbook, which is what makes the required set configuration rather
    than code: the fields a review must state can change without a deployment.
    """

    source_thread_id: str = Field(min_length=1, max_length=255)
    group_key: str = Field(min_length=1, max_length=255)
    subject: str | None = None
    sender: str | None = None
    received_at: datetime | None = None
    summary: str | None = None
    why_it_matters: str | None = None
    recommended_next_action: str | None = None
    recommended_gmail_disposition: Disposition | None = None
    task_required: bool | None = None
    urgency: Urgency | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    uncertainties: list[str] | None = None

    def field_value(self, field: ItemField) -> object:
        """What this item states for a field the playbook requires.

        Written out rather than looked up by name so that a field added to the
        playbook vocabulary and not to this model is a type error here, rather
        than a required field that silently reads as absent.
        """
        match field:
            case ItemField.SOURCE_THREAD_ID:
                return self.source_thread_id
            case ItemField.GROUP_KEY:
                return self.group_key
            case ItemField.SUBJECT:
                return self.subject
            case ItemField.SENDER:
                return self.sender
            case ItemField.RECEIVED_AT:
                return self.received_at
            case ItemField.SUMMARY:
                return self.summary
            case ItemField.WHY_IT_MATTERS:
                return self.why_it_matters
            case ItemField.RECOMMENDED_NEXT_ACTION:
                return self.recommended_next_action
            case ItemField.RECOMMENDED_GMAIL_DISPOSITION:
                return self.recommended_gmail_disposition
            case ItemField.TASK_REQUIRED:
                return self.task_required
            case ItemField.URGENCY:
                return self.urgency
            case ItemField.CONFIDENCE:
                return self.confidence
            case ItemField.UNCERTAINTIES:
                return self.uncertainties


class EmailReviewSubmission(StrictModel):
    review_id: str = Field(min_length=1, max_length=36)
    playbook_version_id: str = Field(min_length=1, max_length=36)
    source_snapshot: SourceSnapshot
    items: list[ReviewedItem] = []
    recommended_order: list[str] = []

    def thread_ids(self) -> list[str]:
        return [item.source_thread_id for item in self.items]


@dataclass(frozen=True)
class Recorded:
    review_id: str
    phase_key: str
    snapshot_id: str
    item_count: int
    counts_by_group: dict[str, int]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Refused:
    review_id: str
    phase_key: str
    failures: tuple[Refusal, ...]


@dataclass(frozen=True)
class Completion:
    review_id: str
    completed_phase: str
    next_phase: str | None
    next_phase_status: str | None
    review_status: ReviewStatus
    message: str


@dataclass(frozen=True)
class PhaseView:
    phase_key: str
    label: str
    status: PhaseStatus
    position: int
    item_count: int
    recorded_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class ReviewView:
    """A review as it stands, without any of the mail it is about."""

    review: GuidedReview
    playbook: ActiveReviewPlaybook
    phases: tuple[PhaseView, ...]
    snapshot: GuidedReviewSnapshot | None
    next_operation: str | None


def start_review(
    session: Session,
    *,
    fresh: bool = True,
    actor: str = ASSISTANT_ACTOR,
    now: datetime | None = None,
    today: date | None = None,
) -> ReviewView:
    """Open a review of the mailbox as it is now, setting any earlier one aside.

    There is no create-or-resume here, because that is what made "hello" at
    half past nine hand back a review of the nine o'clock mailbox. A review is
    a snapshot; asking again asks for a new one. The one it replaces keeps
    everything recorded in it and is named as superseded.
    """
    if not fresh:
        raise ReviewRefused(
            "start_admin_review always starts a fresh review. To look at one "
            "already under way, call read_admin_review with its review_id."
        )

    moment = now or datetime.now(UTC)
    playbook = read_active_review_playbook(session, now=moment)

    superseded = set_aside_open_reviews(session, actor=actor, now=moment)

    review = GuidedReview(
        review_date=today or moment.date(),
        status=ReviewStatus.IN_PROGRESS,
        playbook_id=playbook.document.playbook_id,
        playbook_revision_id=playbook.revision.id,
        snapshot_at=moment,
        supersedes_review_id=superseded[-1].id if superseded else None,
    )
    session.add(review)
    session.flush()

    for position, phase in enumerate(playbook.document.ordered(), start=1):
        kind = known_phase(phase.phase_key)
        implemented = kind is not None and kind.implemented
        session.add(
            GuidedReviewPhase(
                review_id=review.id,
                phase_key=phase.phase_key,
                label=phase.label,
                position=position,
                status=PhaseStatus.READY if implemented else PhaseStatus.UNAVAILABLE,
            )
        )

    review.current_phase_key = first_available(playbook)
    session.flush()

    record_event(
        session,
        review,
        EventKind.REVIEW_STARTED,
        actor=actor,
        detail={
            "review_date": review.review_date.isoformat(),
            "snapshot_at": moment.isoformat(),
            "supersedes_review_id": review.supersedes_review_id,
        },
    )
    record_event(
        session,
        review,
        EventKind.PLAYBOOK_LOADED,
        actor=actor,
        detail={
            "playbook_id": review.playbook_id,
            "playbook_version_id": review.playbook_revision_id,
            "revision_number": playbook.revision.number,
        },
    )
    if review.current_phase_key is not None:
        record_event(
            session,
            review,
            EventKind.PHASE_STARTED,
            actor=actor,
            phase_key=review.current_phase_key,
        )
        started = read_phase_row(session, review.id, review.current_phase_key)
        started.started_at = moment

    session.flush()
    logger.info(
        "guided review %s started under revision %s, superseding %s",
        review.id,
        review.playbook_revision_id,
        review.supersedes_review_id or "nothing",
    )
    return build_view(session, review, playbook)


def set_aside_open_reviews(
    session: Session, *, actor: str, now: datetime
) -> list[GuidedReview]:
    """Close off any review still open, so only one is current at a time."""
    open_reviews = list(
        session.execute(
            select(GuidedReview)
            .where(
                GuidedReview.status.in_(
                    [ReviewStatus.IN_PROGRESS, ReviewStatus.PARTIALLY_COMPLETE]
                )
            )
            .order_by(GuidedReview.started_at)
        )
        .scalars()
        .all()
    )
    for earlier in open_reviews:
        earlier.status = ReviewStatus.SUPERSEDED
        earlier.superseded_at = now
        record_event(
            session,
            earlier,
            EventKind.REVIEW_SUPERSEDED,
            actor=actor,
            detail={"reason": "A fresh review was started."},
        )
    session.flush()
    return open_reviews


def read_review(session: Session, review_id: str) -> ReviewView:
    review = read_review_row(session, review_id)
    playbook = read_pinned_review_playbook(session, review.playbook_revision_id)
    return build_view(session, review, playbook)


def read_phase_playbook(
    session: Session, review_id: str, phase_key: str
) -> tuple[ReviewView, PhaseConfig]:
    """The phase configuration this review is held to, whatever is in force now."""
    view = read_review(session, review_id)
    phase = view.playbook.document.phase(phase_key)
    if phase is None:
        known = ", ".join(known.phase_key for known in view.playbook.document.ordered())
        raise ReviewRefused(
            f"This review has no phase {phase_key!r}. Its phases are: {known}."
        )
    return view, phase


def record_email_review(
    session: Session,
    submission: EmailReviewSubmission,
    *,
    actor: str = ASSISTANT_ACTOR,
    now: datetime | None = None,
) -> Recorded | Refused:
    """Hold a submitted email review to the playbook the review pinned.

    Every check runs before anything is written, and the whole submission is
    either recorded or refused. A refusal is recorded too — what was wrong with
    it is part of the review's history, not just an error the caller saw once.
    """
    moment = now or datetime.now(UTC)
    review = read_review_row(session, submission.review_id)
    playbook = read_pinned_review_playbook(session, review.playbook_revision_id)
    config = playbook.document.phase(EMAIL_REVIEW)
    if config is None:
        raise ReviewRefused(
            "The playbook this review pinned has no email review phase, so there "
            "is nothing to record against."
        )
    phase = read_phase_row(session, review.id, EMAIL_REVIEW)

    failures = check_submission(review, phase, config, submission)
    if failures:
        record_event(
            session,
            review,
            EventKind.VALIDATION_FAILED,
            actor=actor,
            phase_key=EMAIL_REVIEW,
            detail={
                "failures": [
                    {"code": failure.code, "path": failure.path, "message": failure.message}
                    for failure in failures
                ],
                "item_count": len(submission.items),
            },
        )
        session.flush()
        logger.info(
            "guided review %s refused a submission: %s",
            review.id,
            "; ".join(failure.code for failure in failures),
        )
        return Refused(
            review_id=review.id, phase_key=EMAIL_REVIEW, failures=tuple(failures)
        )

    for earlier in read_snapshots(session, phase.id):
        earlier.superseded_at = moment

    snapshot = GuidedReviewSnapshot(
        review_id=review.id,
        phase_id=phase.id,
        source=submission.source_snapshot.source,
        mailbox_scope=submission.source_snapshot.mailbox_scope,
        observed_at=submission.source_snapshot.observed_at,
        thread_count=submission.source_snapshot.thread_count,
        item_count=len(submission.items),
    )
    session.add(snapshot)
    session.flush()

    positions = {thread: index for index, thread in enumerate(submission.recommended_order, 1)}
    for item in submission.items:
        session.add(
            GuidedReviewItem(
                review_id=review.id,
                phase_id=phase.id,
                snapshot_id=snapshot.id,
                source_thread_id=item.source_thread_id,
                group_key=item.group_key,
                position=positions.get(item.source_thread_id),
                subject=item.subject,
                sender=item.sender,
                received_at=item.received_at,
                summary=item.summary,
                why_it_matters=item.why_it_matters,
                recommended_next_action=item.recommended_next_action,
                recommended_disposition=item.recommended_gmail_disposition,
                task_required=item.task_required,
                urgency=item.urgency,
                confidence=item.confidence,
                uncertainties=list(item.uncertainties or []),
            )
        )

    phase.status = PhaseStatus.RECORDED
    phase.recorded_at = moment
    counts = counts_by_group(submission, config)
    warnings = observations(submission, config)

    record_event(
        session,
        review,
        EventKind.SOURCE_SNAPSHOT_RECORDED,
        actor=actor,
        phase_key=EMAIL_REVIEW,
        detail={
            "snapshot_id": snapshot.id,
            "source": snapshot.source,
            "mailbox_scope": snapshot.mailbox_scope,
            "observed_at": submission.source_snapshot.observed_at.isoformat(),
            "thread_count": snapshot.thread_count,
        },
    )
    record_event(
        session,
        review,
        EventKind.EMAIL_REVIEW_RECORDED,
        actor=actor,
        phase_key=EMAIL_REVIEW,
        detail={
            "snapshot_id": snapshot.id,
            "item_count": len(submission.items),
            "counts_by_group": counts,
            "warnings": list(warnings),
        },
    )
    session.flush()
    logger.info(
        "guided review %s recorded %d items in snapshot %s",
        review.id,
        len(submission.items),
        snapshot.id,
    )
    return Recorded(
        review_id=review.id,
        phase_key=EMAIL_REVIEW,
        snapshot_id=snapshot.id,
        item_count=len(submission.items),
        counts_by_group=counts,
        warnings=warnings,
    )


def check_submission(
    review: GuidedReview,
    phase: GuidedReviewPhase,
    config: PhaseConfig,
    submission: EmailReviewSubmission,
) -> list[Refusal]:
    """Everything that has to be true before a review result can be believed."""
    failures: list[Refusal] = []

    if review.status != ReviewStatus.IN_PROGRESS:
        failures.append(
            Refusal(
                code=RefusalCode.REVIEW_NOT_ACTIVE,
                path="review_id",
                message=(
                    f"This review is {review.status}, so nothing can be recorded "
                    "against it. Start a fresh review."
                ),
            )
        )

    if phase.status == PhaseStatus.COMPLETED:
        failures.append(
            Refusal(
                code=RefusalCode.WRONG_PHASE,
                path="review_id",
                message=(
                    "The email review phase is already completed. A correction to a "
                    "completed phase is a new review, not an edit of this one."
                ),
            )
        )
    elif review.current_phase_key != EMAIL_REVIEW:
        failures.append(
            Refusal(
                code=RefusalCode.WRONG_PHASE,
                path="review_id",
                message=(
                    f"This review is on {review.current_phase_key!r}, not the email "
                    "review phase."
                ),
            )
        )

    if submission.playbook_version_id != review.playbook_revision_id:
        failures.append(
            Refusal(
                code=RefusalCode.STALE_PLAYBOOK_VERSION,
                path="playbook_version_id",
                message=(
                    "This review is held to playbook version "
                    f"{review.playbook_revision_id!r} and the submission names "
                    f"{submission.playbook_version_id!r}. Read the playbook again and "
                    "classify against the version this review pinned."
                ),
            )
        )

    source = config.source
    if source is not None:
        if submission.source_snapshot.source != source.app:
            failures.append(
                Refusal(
                    code=RefusalCode.WRONG_SCOPE,
                    path="source_snapshot.source",
                    message=(
                        f"This phase reads {source.app}, and the snapshot says "
                        f"{submission.source_snapshot.source}."
                    ),
                )
            )
        if submission.source_snapshot.mailbox_scope != source.mailbox_scope:
            failures.append(
                Refusal(
                    code=RefusalCode.WRONG_SCOPE,
                    path="source_snapshot.mailbox_scope",
                    message=(
                        f"This phase is {source.mailbox_scope}, and the snapshot says "
                        f"{submission.source_snapshot.mailbox_scope}. A review of a "
                        "different scope is a review of different mail."
                    ),
                )
            )

    if config.completion_criteria.catch_all_required and config.catch_all() is None:
        failures.append(
            Refusal(
                code=RefusalCode.NO_CATCH_ALL,
                path="playbook_version_id",
                message=(
                    "The pinned playbook has no catch-all group, so a thread that "
                    "fits nowhere has nowhere honest to go."
                ),
            )
        )

    failures.extend(check_items(config, submission))
    failures.extend(check_counts(submission))
    failures.extend(check_order(config, submission))
    return failures


def check_items(
    config: PhaseConfig, submission: EmailReviewSubmission
) -> list[Refusal]:
    failures: list[Refusal] = []
    required = config.required_fields()

    for index, item in enumerate(submission.items):
        path = f"items[{index}]"
        if config.group(item.group_key) is None:
            groups = ", ".join(group.key for group in config.ordered_groups())
            failures.append(
                Refusal(
                    code=RefusalCode.UNKNOWN_GROUP,
                    path=f"{path}.group_key",
                    message=(
                        f"{item.group_key!r} is not a group in this playbook version. "
                        f"The groups are: {groups}."
                    ),
                )
            )
        for field in required:
            if not stated(item.field_value(field)):
                failures.append(
                    Refusal(
                        code=RefusalCode.MISSING_FIELD,
                        path=f"{path}.{field.value}",
                        message=(
                            f"{item.source_thread_id} has no {field.value}, which this "
                            "playbook version requires of every reviewed thread."
                        ),
                    )
                )
    return failures


def stated(value: object) -> bool:
    """Present and said. An empty string is a field left blank, not an answer.

    `uncertainties` is the exception that proves it: an empty list is a real
    answer — nothing uncertain — where an empty summary is a missing summary.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def check_counts(submission: EmailReviewSubmission) -> list[Refusal]:
    failures: list[Refusal] = []
    thread_ids = submission.thread_ids()
    seen: set[str] = set()
    for index, thread_id in enumerate(thread_ids):
        if thread_id in seen:
            failures.append(
                Refusal(
                    code=RefusalCode.DUPLICATE_THREAD,
                    path=f"items[{index}].source_thread_id",
                    message=(
                        f"{thread_id} is reviewed twice. Every Inbox thread belongs to "
                        "exactly one group, once."
                    ),
                )
            )
        seen.add(thread_id)

    if submission.source_snapshot.thread_count != len(seen):
        failures.append(
            Refusal(
                code=RefusalCode.COUNT_MISMATCH,
                path="source_snapshot.thread_count",
                message=(
                    f"The snapshot says {submission.source_snapshot.thread_count} "
                    f"threads were read and {len(seen)} were submitted. A review of "
                    "some of the Inbox must not be recorded as a review of it."
                ),
            )
        )
    return failures


def check_order(
    config: PhaseConfig, submission: EmailReviewSubmission
) -> list[Refusal]:
    if config.sorting.recommended_order != "required":
        return []

    failures: list[Refusal] = []
    thread_ids = set(submission.thread_ids())
    seen: set[str] = set()

    for index, thread_id in enumerate(submission.recommended_order):
        path = f"recommended_order[{index}]"
        if thread_id not in thread_ids:
            failures.append(
                Refusal(
                    code=RefusalCode.ORDER_UNKNOWN_ITEM,
                    path=path,
                    message=f"{thread_id} is in the recommended order and not in the review.",
                )
            )
        if thread_id in seen:
            failures.append(
                Refusal(
                    code=RefusalCode.ORDER_DUPLICATE,
                    path=path,
                    message=f"{thread_id} appears twice in the recommended order.",
                )
            )
        seen.add(thread_id)

    missing = sorted(thread_ids - seen)
    if missing:
        failures.append(
            Refusal(
                code=RefusalCode.ORDER_INCOMPLETE,
                path="recommended_order",
                message=(
                    f"{len(missing)} reviewed thread(s) are not in the recommended "
                    f"order: {', '.join(missing[:5])}"
                    + ("…" if len(missing) > 5 else "")
                ),
            )
        )
    return failures


def counts_by_group(
    submission: EmailReviewSubmission, config: PhaseConfig
) -> dict[str, int]:
    """Counts in the playbook's group order, so a caller can render them as-is."""
    counts = {group.key: 0 for group in config.ordered_groups()}
    for item in submission.items:
        counts[item.group_key] = counts.get(item.group_key, 0) + 1
    if not config.rendering.show_empty_groups:
        return {key: count for key, count in counts.items() if count}
    return counts


def observations(
    submission: EmailReviewSubmission, config: PhaseConfig
) -> tuple[str, ...]:
    """What is worth saying about an accepted review without refusing it.

    None of these is a fault. They are the things a reader should know before
    acting on the result: what the review was unsure of, and what it wants done
    to the mailbox — which nothing here has done.
    """
    warnings: list[str] = []
    catch_all = config.catch_all()

    if catch_all is not None:
        unplaced = [item for item in submission.items if item.group_key == catch_all.key]
        if unplaced:
            warnings.append(
                f"{len(unplaced)} thread(s) went to {catch_all.label!r} because no other "
                "group was clearly right."
            )

    unsure = [
        item
        for item in submission.items
        if item.confidence is not None and item.confidence < LOW_CONFIDENCE
    ]
    if unsure:
        warnings.append(
            f"{len(unsure)} thread(s) were classified below {LOW_CONFIDENCE:.0%} confidence."
        )

    uncertain = [item for item in submission.items if item.uncertainties]
    if uncertain:
        warnings.append(f"{len(uncertain)} thread(s) recorded something they were unsure of.")

    changing = [
        item
        for item in submission.items
        if item.recommended_gmail_disposition in MAILBOX_CHANGING
    ]
    if changing:
        warnings.append(
            f"{len(changing)} thread(s) recommend a change to the mailbox. Nothing has "
            "been done in Gmail: these are recommendations, and executing one is a "
            "separate request Brian confirms."
        )
    return tuple(warnings)


def complete_phase(
    session: Session,
    review_id: str,
    phase_key: str,
    *,
    actor: str = ASSISTANT_ACTOR,
    now: datetime | None = None,
) -> Completion:
    """Finish a phase whose result has been recorded, and say what follows it.

    Finishing the phase Admin OS implements is not finishing the review. The
    review stays open in a state that says so, because reporting a process as
    complete when three quarters of it was never built is the lie this whole
    milestone is arranged to avoid.
    """
    moment = now or datetime.now(UTC)
    review = read_review_row(session, review_id)
    phase = read_phase_row(session, review.id, phase_key)

    if review.status != ReviewStatus.IN_PROGRESS:
        raise ReviewRefused(
            f"This review is {review.status}, so its phases cannot be completed."
        )
    if phase.status == PhaseStatus.UNAVAILABLE:
        raise ReviewRefused(
            f"{phase.label} is not implemented, so it cannot be completed. It is "
            "named in the review as unavailable and stays that way."
        )
    if phase.status == PhaseStatus.COMPLETED:
        raise ReviewRefused(f"{phase.label} is already completed.")
    if phase.status != PhaseStatus.RECORDED:
        raise ReviewRefused(
            f"{phase.label} has no recorded result, so there is nothing to complete. "
            "Submit the review with record_email_review first."
        )

    phase.status = PhaseStatus.COMPLETED
    phase.completed_at = moment
    record_event(
        session, review, EventKind.PHASE_COMPLETED, actor=actor, phase_key=phase_key
    )

    following = next_phase(session, review, phase.position)
    remaining = [
        row
        for row in read_phase_rows(session, review.id)
        if row.status not in {PhaseStatus.COMPLETED, PhaseStatus.UNAVAILABLE}
    ]

    if remaining:
        review.status = ReviewStatus.IN_PROGRESS
        review.current_phase_key = remaining[0].phase_key
        message = f"{phase.label} is complete. Next: {remaining[0].label}."
    elif any(row.status == PhaseStatus.UNAVAILABLE for row in read_phase_rows(session, review.id)):
        review.status = ReviewStatus.PARTIALLY_COMPLETE
        review.current_phase_key = None
        waiting = [
            row.label
            for row in read_phase_rows(session, review.id)
            if row.status == PhaseStatus.UNAVAILABLE
        ]
        message = (
            f"{phase.label} is complete. The review is not: "
            f"{listed(waiting)} {'is' if len(waiting) == 1 else 'are'} not "
            "implemented yet."
        )
    else:
        review.status = ReviewStatus.COMPLETED
        review.completed_at = moment
        review.current_phase_key = None
        message = f"{phase.label} is complete, and so is the review."

    session.flush()
    logger.info("guided review %s completed phase %s", review.id, phase_key)
    return Completion(
        review_id=review.id,
        completed_phase=phase_key,
        next_phase=following.phase_key if following is not None else None,
        next_phase_status=PhaseStatus(following.status) if following is not None else None,
        review_status=ReviewStatus(review.status),
        message=message,
    )


def listed(labels: Sequence[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def next_phase(
    session: Session, review: GuidedReview, position: int
) -> GuidedReviewPhase | None:
    for row in read_phase_rows(session, review.id):
        if row.position > position:
            return row
    return None


def first_available(playbook: ActiveReviewPlaybook) -> str | None:
    for phase in playbook.document.ordered():
        kind = known_phase(phase.phase_key)
        if kind is not None and kind.implemented:
            return phase.phase_key
    return None


def build_view(
    session: Session, review: GuidedReview, playbook: ActiveReviewPlaybook
) -> ReviewView:
    rows = read_phase_rows(session, review.id)
    current = None
    if review.current_phase_key is not None:
        current = next(
            (row for row in rows if row.phase_key == review.current_phase_key), None
        )
    snapshot = current_snapshot(session, current.id) if current is not None else None

    views = tuple(
        PhaseView(
            phase_key=row.phase_key,
            label=row.label,
            status=PhaseStatus(row.status),
            position=row.position,
            item_count=item_count(session, row.id),
            recorded_at=row.recorded_at,
            completed_at=row.completed_at,
        )
        for row in rows
    )
    return ReviewView(
        review=review,
        playbook=playbook,
        phases=views,
        snapshot=snapshot,
        next_operation=next_operation(review, current),
    )


def next_operation(review: GuidedReview, current: GuidedReviewPhase | None) -> str | None:
    """The one thing a caller may usefully do next, or nothing at all."""
    if review.status in {ReviewStatus.SUPERSEDED, ReviewStatus.COMPLETED}:
        return None
    if review.status == ReviewStatus.PARTIALLY_COMPLETE or current is None:
        return None
    if current.status == PhaseStatus.READY:
        return "read_review_playbook"
    if current.status == PhaseStatus.RECORDED:
        return "complete_review_phase"
    return None


def read_review_row(session: Session, review_id: str) -> GuidedReview:
    review = session.get(GuidedReview, review_id)
    if review is None:
        raise ReviewNotFound(f"No review {review_id!r} exists.")
    return review


def read_phase_rows(session: Session, review_id: str) -> list[GuidedReviewPhase]:
    return list(
        session.execute(
            select(GuidedReviewPhase)
            .where(GuidedReviewPhase.review_id == review_id)
            .order_by(GuidedReviewPhase.position)
        )
        .scalars()
        .all()
    )


def read_phase_row(session: Session, review_id: str, phase_key: str) -> GuidedReviewPhase:
    phase = (
        session.execute(
            select(GuidedReviewPhase).where(
                GuidedReviewPhase.review_id == review_id,
                GuidedReviewPhase.phase_key == phase_key,
            )
        )
        .scalars()
        .first()
    )
    if phase is None:
        raise ReviewRefused(f"This review has no phase {phase_key!r}.")
    return phase


def read_snapshots(session: Session, phase_id: str) -> list[GuidedReviewSnapshot]:
    return list(
        session.execute(
            select(GuidedReviewSnapshot).where(
                GuidedReviewSnapshot.phase_id == phase_id,
                GuidedReviewSnapshot.superseded_at.is_(None),
            )
        )
        .scalars()
        .all()
    )


def current_snapshot(session: Session, phase_id: str) -> GuidedReviewSnapshot | None:
    snapshots = read_snapshots(session, phase_id)
    return snapshots[-1] if snapshots else None


def item_count(session: Session, phase_id: str) -> int:
    snapshot = current_snapshot(session, phase_id)
    return snapshot.item_count if snapshot is not None else 0


def read_items(session: Session, snapshot_id: str) -> list[GuidedReviewItem]:
    return list(
        session.execute(
            select(GuidedReviewItem)
            .where(GuidedReviewItem.snapshot_id == snapshot_id)
            .order_by(GuidedReviewItem.position)
        )
        .scalars()
        .all()
    )


def read_events(session: Session, review_id: str) -> list[GuidedReviewEvent]:
    return list(
        session.execute(
            select(GuidedReviewEvent)
            .where(GuidedReviewEvent.review_id == review_id)
            .order_by(GuidedReviewEvent.sequence)
        )
        .scalars()
        .all()
    )


def record_event(
    session: Session,
    review: GuidedReview,
    kind: EventKind,
    *,
    actor: str,
    phase_key: str | None = None,
    detail: dict[str, object] | None = None,
) -> GuidedReviewEvent:
    event = GuidedReviewEvent(
        review_id=review.id,
        sequence=next_sequence(session, review.id),
        phase_key=phase_key,
        kind=kind,
        actor=actor,
        detail=detail,
    )
    session.add(event)
    session.flush()
    return event


def next_sequence(session: Session, review_id: str) -> int:
    highest = (
        session.execute(
            select(func.max(GuidedReviewEvent.sequence)).where(
                GuidedReviewEvent.review_id == review_id
            )
        )
        .scalars()
        .first()
    )
    return (highest or 0) + 1


def counts_for(session: Session, phase_id: str) -> dict[str, int]:
    """What was recorded, by group, read back from what was stored."""
    snapshot = current_snapshot(session, phase_id)
    if snapshot is None:
        return {}
    counts: dict[str, int] = {}
    for item in read_items(session, snapshot.id):
        counts[item.group_key] = counts.get(item.group_key, 0) + 1
    return counts


def phase_sequence(views: Sequence[PhaseView]) -> list[dict[str, str]]:
    return [{"phase_key": view.phase_key, "status": view.status} for view in views]


__all__ = [
    "ASSISTANT_ACTOR",
    "Completion",
    "EmailReviewSubmission",
    "EventKind",
    "PhaseStatus",
    "PhaseView",
    "Recorded",
    "Refusal",
    "RefusalCode",
    "Refused",
    "ReviewNotFound",
    "ReviewRefused",
    "ReviewStatus",
    "ReviewView",
    "ReviewedItem",
    "SourceSnapshot",
    "complete_phase",
    "counts_for",
    "read_events",
    "read_items",
    "read_phase_playbook",
    "read_review",
    "record_email_review",
    "start_review",
]
