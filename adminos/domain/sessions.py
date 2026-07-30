"""A session: one run of the playbook against the state of the day.

The playbook is the process and the session is the working of it. Waking up
takes the playbook in force, holds on to that exact revision for the whole
session, and works the activities it names in the order it names them.

Two things this is careful about. An activity the playbook names and Admin OS
cannot yet do is reported as exactly that rather than skipped quietly. And
"skip Legal today" is recorded on the session, never on the playbook: a
correction is about a morning until Brian says otherwise.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.capabilities.config import LoadedCapabilities
from adminos.db.models import AssistantSession, ReviewRun, SessionActivity
from adminos.domain.activities import ActivityKind, known_activity
from adminos.domain.mailboxes import DEFAULT_SCOPE, ReviewScope
from adminos.domain.plan import PlanRefused, ReviewSummary, begin_plan, review_summary
from adminos.domain.playbook import ActivityConfig, read_playbook
from adminos.domain.playbook_store import ActivePlaybook, read_revision
from adminos.domain.review import (
    RunState,
    RunView,
    open_fresh_review,
    read_run,
    refresh_states,
)
from adminos.logging import get_logger


logger = get_logger(__name__)

EMAIL_ACTIVITY = "email_review"
CLOSEOUT_ACTIVITY = "session_closeout"


class SessionStatus(StrEnum):
    PROPOSED = "proposed"
    """Opened, its plan stated, waiting to be told to begin."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class ActivityState(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    """Set aside for this session, by Brian, at the open."""

    UNAVAILABLE = "unavailable"
    """In the playbook, and not built here yet."""


class SessionNotFound(LookupError):
    """Raised when there is no session to continue or to read."""


class SessionRefused(RuntimeError):
    """Raised when a session cannot do what was asked of it."""


@dataclass(frozen=True)
class StepView:
    """One step of an activity, and how much of it there is where that is known."""

    key: str
    label: str
    position: int
    count: int | None = None
    state: str | None = None


@dataclass(frozen=True)
class ActivityView:
    row: SessionActivity
    kind: ActivityKind
    steps: tuple[StepView, ...]
    intro: str


@dataclass(frozen=True)
class SessionView:
    """A session as it stands: its plan, where it is, and the work in hand."""

    row: AssistantSession
    playbook: ActivePlaybook
    activities: tuple[ActivityView, ...]
    review: RunView | None = None
    """The email review, where this session has one."""

    summary: ReviewSummary | None = None
    """What the session has done, counted from verified execution."""

    superseded: AssistantSession | None = None

    def current(self) -> ActivityView | None:
        if self.row.current_activity_key is None:
            return None
        return self.find(self.row.current_activity_key)

    def find(self, activity_key: str) -> ActivityView | None:
        for activity in self.activities:
            if activity.row.activity_key == activity_key:
                return activity
        return None

    def workable(self) -> tuple[ActivityView, ...]:
        return tuple(
            activity
            for activity in self.activities
            if activity.row.state not in {ActivityState.SKIPPED, ActivityState.UNAVAILABLE}
        )


def open_session(
    session: Session,
    loaded: LoadedCapabilities,
    playbook: ActivePlaybook,
    scope: ReviewScope = DEFAULT_SCOPE,
    evidence_refresh_at: datetime | None = None,
    order: Sequence[str] | None = None,
    only: Sequence[str] | None = None,
    skip: Sequence[str] | None = None,
    now: datetime | None = None,
) -> SessionView:
    """Wake up: take the playbook in force and state what this session will do.

    The session that was open is set aside rather than resumed, for the reason
    a review is: the mailbox, the calendar and the day have all moved, and a
    session handed back an hour later is an answer about an hour ago. What it
    keeps is everything it recorded.

    Nothing is presented here. The plan is stated and the session waits, unless
    the playbook says to start on its own.
    """
    moment = now or datetime.now(UTC)
    superseded = read_open_session(session, playbook.revision.playbook_id)
    if superseded is not None:
        abandon_session(session, superseded, moment)

    configured = [activity.activity_key for activity in playbook.document.enabled()]
    sequence = arrange(configured, order)
    set_aside = sidelined(configured, sequence, only, skip)
    if not [key for key in sequence if key not in set_aside]:
        raise SessionRefused("A session with every activity set aside has nothing to do.")

    row = AssistantSession(
        playbook_id=playbook.revision.playbook_id,
        playbook_revision_id=playbook.revision.id,
        status=SessionStatus.PROPOSED,
        sequence=sequence,
        skipped=sorted(set_aside),
        overrides=describe_overrides(playbook, sequence, set_aside),
        supersedes_session_id=superseded.id if superseded is not None else None,
    )
    session.add(row)
    session.flush()

    review: RunView | None = None
    for position, key in enumerate(sequence, start=1):
        configured_activity = playbook.document.find(key)
        kind = known_activity(key)
        if configured_activity is None or kind is None:
            continue
        state = activity_state(kind, key in set_aside)
        activity = SessionActivity(
            session_id=row.id,
            activity_key=key,
            label=configured_activity.label,
            position=position,
            state=state,
            intro=configured_activity.intro,
            steps=[step.capability_key for step in configured_activity.enabled_steps()],
        )
        if key == EMAIL_ACTIVITY and state == ActivityState.PENDING:
            review = open_fresh_review(
                session,
                loaded,
                now=moment,
                scope=scope,
                evidence_refresh_at=evidence_refresh_at,
            )
            activity.run_id = review.run.id
        session.add(activity)

    session.flush()
    logger.info(
        "session %s opened on playbook revision %s, working %s",
        row.id,
        playbook.revision.id,
        ", ".join(key for key in sequence if key not in set_aside),
    )
    return read_session_view(session, loaded, row, playbook, superseded)


def continue_session(
    session: Session,
    loaded: LoadedCapabilities,
    playbook: ActivePlaybook,
    playbook_id: str | None = None,
) -> SessionView:
    """Pick the session back up, and never start one.

    A session under way keeps the playbook revision it opened with. If the
    playbook has changed since, this session is still the one that was agreed
    to; the new one is what the next session runs.
    """
    row = read_open_session(session, playbook_id or playbook.revision.playbook_id)
    if row is None:
        raise SessionNotFound("No session is under way. Starting one is `startSession`.")
    return read_session_view(session, loaded, row, session_playbook(session, playbook, row))


def begin_session(
    session: Session,
    loaded: LoadedCapabilities,
    row: AssistantSession,
    playbook: ActivePlaybook,
    now: datetime | None = None,
) -> SessionView:
    """Agree the plan and start the first activity that can be worked."""
    moment = now or datetime.now(UTC)
    if row.status == SessionStatus.ABANDONED:
        raise SessionRefused("That session was set aside when a later one was opened.")

    if row.status == SessionStatus.PROPOSED:
        row.status = SessionStatus.IN_PROGRESS
        row.begun_at = moment
        session.flush()

    view = read_session_view(session, loaded, row, playbook)
    activity = view.current() or next(
        (
            candidate
            for candidate in view.workable()
            if candidate.row.state == ActivityState.PENDING
        ),
        None,
    )
    if activity is None:
        return advance_session(session, loaded, row, playbook, moment)
    return begin_activity(session, loaded, row, playbook, activity.row.activity_key, moment)


def begin_activity(
    session: Session,
    loaded: LoadedCapabilities,
    row: AssistantSession,
    playbook: ActivePlaybook,
    activity_key: str,
    now: datetime | None = None,
) -> SessionView:
    """Start one activity, in the order and with the steps the playbook says.

    This is where the playbook stops being a document. The email review's
    groups are worked in the order the playbook lists its steps, and a step
    turned off there is a group set aside for this review — the same mechanism
    "skip Legal today" uses, reached from configuration instead of from a
    sentence.
    """
    moment = now or datetime.now(UTC)
    activity = read_activity(session, row, activity_key)
    kind = known_activity(activity_key)
    if kind is None:
        raise SessionRefused(f"{activity_key!r} is not an activity this service knows.")
    if activity.state == ActivityState.UNAVAILABLE:
        raise SessionRefused(
            f"{activity.label} is in the playbook and is not built here yet, so there "
            "is nothing to work. The session can move past it."
        )
    if activity.state == ActivityState.SKIPPED:
        raise SessionRefused(f"{activity.label} was set aside for this session.")

    if row.status == SessionStatus.PROPOSED:
        row.status = SessionStatus.IN_PROGRESS
        row.begun_at = moment

    if activity.state == ActivityState.PENDING:
        activity.state = ActivityState.IN_PROGRESS
        activity.started_at = moment
    row.current_activity_key = activity_key
    session.flush()

    if activity_key == EMAIL_ACTIVITY and activity.run_id is not None:
        configured = playbook.document.find(EMAIL_ACTIVITY)
        run = read_run(session, activity.run_id)
        try:
            begin_plan(
                session,
                loaded,
                run,
                order=list(activity.steps),
                skip=turned_off(configured, loaded) if configured else None,
                now=moment,
            )
        except PlanRefused as exc:
            raise SessionRefused(str(exc)) from exc

    return read_session_view(session, loaded, row, playbook)


def advance_session(
    session: Session,
    loaded: LoadedCapabilities,
    row: AssistantSession,
    playbook: ActivePlaybook,
    now: datetime | None = None,
) -> SessionView:
    """Move to the next activity, once the one in hand is done with.

    Done with is asked of the work rather than asserted by the session: the
    email activity is finished when its review is, which is a question about
    rows Brian settled and actions Gmail confirmed.
    """
    moment = now or datetime.now(UTC)
    for activity in read_activities(session, row):
        if activity.state == ActivityState.IN_PROGRESS and finished(session, loaded, activity):
            activity.state = ActivityState.COMPLETED
            activity.completed_at = moment

    remaining = [
        activity
        for activity in read_activities(session, row)
        if activity.state in {ActivityState.PENDING, ActivityState.IN_PROGRESS}
    ]
    if not remaining:
        row.status = SessionStatus.COMPLETED
        row.completed_at = moment
        row.current_activity_key = None
        session.flush()
        return read_session_view(session, loaded, row, playbook)

    row.current_activity_key = remaining[0].activity_key
    session.flush()
    return read_session_view(session, loaded, row, playbook)


def finished(session: Session, loaded: LoadedCapabilities, activity: SessionActivity) -> bool:
    """Whether this activity has nothing left in it.

    The closeout is finished by being reached: it reports, and reporting is
    all of it. Everything else is asked of what it is working.
    """
    if activity.activity_key == CLOSEOUT_ACTIVITY:
        return True
    if activity.run_id is None:
        return False
    run = session.get(ReviewRun, activity.run_id)
    if run is None:
        return False
    return RunState(run.state) in {RunState.COMPLETED, RunState.ABANDONED}


def abandon_session(session: Session, row: AssistantSession, now: datetime) -> None:
    """Set a session aside, keeping everything it recorded."""
    row.status = SessionStatus.ABANDONED
    row.abandoned_at = now
    session.flush()


def read_open_session(session: Session, playbook_id: str) -> AssistantSession | None:
    """The session under way, if there is one.

    A completed session is not one: it is a record of a morning, and the way
    back into it is by name.
    """
    return session.execute(
        select(AssistantSession)
        .where(
            AssistantSession.playbook_id == playbook_id,
            AssistantSession.status.in_(
                [SessionStatus.PROPOSED, SessionStatus.IN_PROGRESS]
            ),
        )
        .order_by(AssistantSession.opened_at.desc())
    ).scalars().first()


def read_session(session: Session, session_id: str) -> AssistantSession:
    row = session.get(AssistantSession, session_id)
    if row is None:
        raise SessionNotFound(f"No session {session_id!r}.")
    return row


def read_activities(session: Session, row: AssistantSession) -> list[SessionActivity]:
    return list(
        session.execute(
            select(SessionActivity)
            .where(SessionActivity.session_id == row.id)
            .order_by(SessionActivity.position)
        )
        .scalars()
        .all()
    )


def read_activity(
    session: Session, row: AssistantSession, activity_key: str
) -> SessionActivity:
    for activity in read_activities(session, row):
        if activity.activity_key == activity_key:
            return activity
    raise SessionNotFound(f"This session has no activity {activity_key!r}.")


def session_playbook(
    session: Session, playbook: ActivePlaybook, row: AssistantSession
) -> ActivePlaybook:
    """The playbook this session is running, which may not be the current one."""
    if row.playbook_revision_id == playbook.revision.id:
        return playbook
    revision = read_revision(session, row.playbook_revision_id)
    return ActivePlaybook(
        revision=revision,
        document=read_playbook(dict(revision.document)),
        report=playbook.report,
    )


def read_session_view(
    session: Session,
    loaded: LoadedCapabilities,
    row: AssistantSession,
    playbook: ActivePlaybook,
    superseded: AssistantSession | None = None,
) -> SessionView:
    """The session as it stands, with the work in hand read back from its own state."""
    activities: list[ActivityView] = []
    review: RunView | None = None
    summary: ReviewSummary | None = None

    for activity in read_activities(session, row):
        kind = known_activity(activity.activity_key)
        if kind is None:
            continue
        configured = playbook.document.find(activity.activity_key)
        if activity.run_id is not None:
            run = session.get(ReviewRun, activity.run_id)
            if run is not None:
                review = refresh_states(session, loaded, run)
                summary = review_summary(session, run)
        activities.append(
            ActivityView(
                row=activity,
                kind=kind,
                steps=step_views(activity, configured, review),
                intro=activity.intro or "",
            )
        )

    return SessionView(
        row=row,
        playbook=playbook,
        activities=tuple(activities),
        review=review,
        summary=summary,
        superseded=superseded,
    )


def step_views(
    activity: SessionActivity, configured: ActivityConfig | None, review: RunView | None
) -> tuple[StepView, ...]:
    """The steps of one activity, with counts where the work is real."""
    labels = {step.capability_key: step.label for step in (configured.steps if configured else [])}
    counts: dict[str, int] = {}
    states: dict[str, str] = {}
    if activity.activity_key == EMAIL_ACTIVITY and review is not None:
        for group in review.groups:
            counts[group.group.capability_key] = len(group.items)
            states[group.group.capability_key] = group.group.state

    return tuple(
        StepView(
            key=key,
            label=labels.get(key, key),
            position=position,
            count=counts.get(key),
            state=states.get(key),
        )
        for position, key in enumerate(activity.steps, start=1)
    )


def activity_state(kind: ActivityKind, set_aside: bool) -> ActivityState:
    if set_aside:
        return ActivityState.SKIPPED
    return ActivityState.PENDING if kind.built() else ActivityState.UNAVAILABLE


def turned_off(configured: ActivityConfig, loaded: LoadedCapabilities) -> list[str]:
    """Capabilities the playbook has turned off, as groups to set aside.

    Only ones the review will contain: a playbook that names a capability
    nobody configured is invalid and never reaches here, but a capability
    disabled in `capabilities.yaml` is simply not in the review to skip.
    """
    enabled = {step.capability_key for step in configured.enabled_steps()}
    return [
        capability.key for capability in loaded.enabled() if capability.key not in enabled
    ]


def arrange(configured: Sequence[str], order: Sequence[str] | None) -> list[str]:
    """The whole sequence, with anything Brian named brought to the front."""
    if not order:
        return list(configured)
    check_known(configured, order)
    named = list(dict.fromkeys(order))
    return named + [key for key in configured if key not in named]


def sidelined(
    configured: Sequence[str],
    sequence: Sequence[str],
    only: Sequence[str] | None,
    skip: Sequence[str] | None,
) -> set[str]:
    if only:
        check_known(configured, only)
        wanted = set(only)
        return {key for key in sequence if key not in wanted}
    if skip:
        check_known(configured, skip)
        return set(skip)
    return set()


def check_known(configured: Sequence[str], named: Sequence[str]) -> None:
    unknown = [key for key in named if key not in configured]
    if unknown:
        raise SessionRefused(
            f"The playbook has no activity {', '.join(repr(key) for key in unknown)}. "
            f"It works {', '.join(repr(key) for key in configured)}."
        )


def describe_overrides(
    playbook: ActivePlaybook, sequence: Sequence[str], set_aside: set[str]
) -> list[str]:
    """What was asked for that the playbook does not say, in sentences.

    Written down because this is the boundary the whole design turns on: these
    are changes to a session, and the way they stay that way is by being
    recorded somewhere that is not the playbook.
    """
    said: list[str] = []
    configured = [activity.activity_key for activity in playbook.document.enabled()]
    labels = {
        activity.activity_key: activity.label for activity in playbook.document.activities
    }
    if list(sequence) != configured:
        said.append(
            "This session works "
            + ", ".join(labels.get(key, key) for key in sequence)
            + ", which is not the playbook's order."
        )
    for key in sorted(set_aside):
        said.append(f"{labels.get(key, key)} is set aside for this session only.")
    return said


__all__ = [
    "ActivityState",
    "ActivityView",
    "SessionNotFound",
    "SessionRefused",
    "SessionStatus",
    "SessionView",
    "StepView",
    "advance_session",
    "begin_activity",
    "begin_session",
    "continue_session",
    "open_session",
    "read_session",
    "read_session_view",
]
