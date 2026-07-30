"""A session: the whole of an admin interaction, in the order the playbook says.

"Good morning" opens a session. The session states its plan — the activities,
their order, and what the first one consists of — and waits. Beginning it works
the first activity; the email review is one of them, and the same review
lifecycle as before is what does the work.

The words Admin OS opens with are its own, and so is the plan it reads out. A
plan composed by whatever is doing the talking is a plan nobody agreed to and
no test can hold it to.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.api.deps import read_capability_config
from adminos.api.playbook import (
    RevisionResponse,
    ValidationResponse,
    build_revision_response,
    build_validation_response,
)
from adminos.api.review import (
    EvidenceRefresh,
    RunResponse,
    build_run_response,
    refresh_evidence,
    requested_scope,
)
from adminos.api.security import require_api_key
from adminos.capabilities.config import LoadedCapabilities
from adminos.db.engine import DatabaseNotConfigured, session_scope
from adminos.db.models import AssistantSession, PlaybookRevision
from adminos.domain.evidence import DEFAULT_SYNC_LIMIT, MAX_SYNC_LIMIT
from adminos.domain.mailboxes import SCOPE_NAMES, ReviewScope
from adminos.domain.plan import OpeningMode, ReviewSummary
from adminos.domain.playbook import DEFAULT_PLAYBOOK_ID, PlaybookError
from adminos.domain.playbook_store import (
    ActivePlaybook,
    RevisionRefused,
    RevisionStatus,
    read_active_playbook,
)
from adminos.domain.sessions import (
    CLOSEOUT_ACTIVITY,
    ActivityState,
    SessionNotFound,
    SessionRefused,
    SessionStatus,
    SessionView,
    advance_session,
    begin_activity,
    begin_session,
    continue_session,
    open_session,
    read_session,
    read_session_view,
    session_playbook,
)
from adminos.logging import get_logger


logger = get_logger(__name__)
router = APIRouter(prefix="/session", tags=["session"])


class StartSessionRequest(BaseModel):
    """How this session should run, where Brian wants it run differently.

    `order`, `only` and `skip` name activity keys and are about this session
    alone. Changing the playbook is `proposePlaybookChange`, and it takes a
    confirmation: a sentence said once in a morning does not become how every
    morning works.
    """

    sync: bool = True
    limit: int = Field(default=DEFAULT_SYNC_LIMIT, ge=1, le=MAX_SYNC_LIMIT)
    scope: str | None = Field(
        default=None,
        description=(
            "Which mail the email activity reviews. Omit it for the inbox; "
            f"naming one of {SCOPE_NAMES} reviews that instead."
        ),
    )
    order: list[str] | None = Field(
        default=None,
        description="Activity keys to work first, in this order, for this session only.",
    )
    only: list[str] | None = Field(
        default=None,
        description="Work only these activities this session; set the rest aside.",
    )
    skip: list[str] | None = Field(
        default=None,
        description="Set these activities aside for this session only.",
    )
    playbook_id: str = DEFAULT_PLAYBOOK_ID


class ContinueSessionRequest(BaseModel):
    playbook_id: str = DEFAULT_PLAYBOOK_ID


class SessionStepResponse(BaseModel):
    key: str
    label: str
    position: int
    count: int | None = None
    """How many rows there are, where the activity is one that has rows."""
    state: str | None = None


class SessionActivityResponse(BaseModel):
    activity_key: str
    label: str
    position: int
    state: str
    """`pending`, `in_progress`, `completed`, `skipped` for this session, or
    `unavailable` where the playbook names something not built here yet."""
    availability: str
    data_source: str
    intro: str
    steps: list[SessionStepResponse]
    review_id: str | None = None


class SessionOpeningResponse(BaseModel):
    mode: str
    text: str


class SessionPlaybookResponse(BaseModel):
    playbook_id: str
    name: str
    revision: RevisionResponse
    validation: ValidationResponse
    """Checked against the capabilities configured now, not when it was written."""


class SessionPlanResponse(BaseModel):
    """What this session will work through, stated before any of it happens."""

    message: str
    """The plan in sentences, safe to read out exactly as written."""

    activities: list[SessionActivityResponse]
    working: list[str]
    skipped: list[str]
    unavailable: list[str]
    current: str | None
    activity_number: int | None
    activity_count: int
    overrides: list[str]
    """What differs from the playbook for this session alone, in sentences."""


class SessionChoiceResponse(BaseModel):
    operation: str
    label: str
    method: str
    path: str


class SessionPromptResponse(BaseModel):
    message: str
    choices: list[SessionChoiceResponse]


class SessionCloseoutResponse(BaseModel):
    """What the session did, and what it did not.

    Activities worked and actions verified are counted separately on purpose.
    A session can be finished with every activity and still owe the mailbox
    every write it decided on, and this is where that is said rather than
    implied.
    """

    activities_completed: list[str]
    activities_skipped: list[str]
    activities_unavailable: list[str]
    items_reviewed: int
    actions_verified: dict[str, int]
    deferred: int
    dismissed: int
    awaiting_execution: int
    """Rows decided whose action the mailbox has not seen."""

    playbook_changes_proposed: list[str]
    playbook_changes_activated: list[str]
    message: str


class SessionResponse(BaseModel):
    session_id: str
    status: str
    opened_at: datetime
    begun_at: datetime | None
    completed_at: datetime | None
    supersedes_session_id: str | None
    playbook: SessionPlaybookResponse
    opening: SessionOpeningResponse | None
    """The playbook's own words, on entry only. Render verbatim, first."""

    plan: SessionPlanResponse
    prompt: SessionPromptResponse | None
    review: RunResponse | None
    """The email activity's review, where this session has one."""

    closeout: SessionCloseoutResponse | None = None
    warnings: list[str] = Field(default_factory=list)


@router.post("/start", response_model=SessionResponse)
async def start_session(
    request: StartSessionRequest,
    _: None = Depends(require_api_key),
) -> SessionResponse:
    """Wake up: load the playbook, read what the day says, and state the plan.

    One call answers "good morning", "let's begin" and "what do I need to do?".
    It takes the playbook in force, holds that revision for the session's whole
    life, reads the mailbox afresh, and says what will be worked through and in
    what order. It presents nothing: the first activity starts when Brian says
    to, unless the playbook says otherwise.

    A session already open is set aside rather than resumed, and keeps
    everything it recorded. Resuming is `continueSession`, and it is his to ask
    for.
    """
    loaded = read_capability_config()
    scope = requested_scope(request.scope)
    refresh = await sync_if_asked(loaded, request, scope)

    with open_database() as session:
        playbook = load_playbook(session, loaded, request.playbook_id)
        try:
            view = open_session(
                session,
                loaded,
                playbook,
                scope=scope,
                evidence_refresh_at=refresh.read_at,
                order=request.order,
                only=request.only,
                skip=request.skip,
            )
        except SessionRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if playbook.document.session.auto_start_first_activity:
            view = begin_session(session, loaded, view.row, playbook)
        return build_session_response(
            session, view, loaded, refresh.warnings, opening=OpeningMode.NEW
        )


@router.post("/continue", response_model=SessionResponse)
def continue_admin_session(
    request: ContinueSessionRequest,
    _: None = Depends(require_api_key),
) -> SessionResponse:
    """Pick the session back up, and never open one.

    "Where was I?" and "let's begin" are different questions. This one has an
    honest answer when there is nothing to resume, rather than quietly starting
    a session nobody asked for.
    """
    loaded = read_capability_config()
    with open_database() as session:
        playbook = load_playbook(session, loaded, request.playbook_id)
        try:
            view = continue_session(session, loaded, playbook, request.playbook_id)
        except SessionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return build_session_response(session, view, loaded, [], opening=OpeningMode.RESUMED)


@router.get("/{session_id}", response_model=SessionResponse)
def read_admin_session(
    session_id: str,
    _: None = Depends(require_api_key),
) -> SessionResponse:
    """Report where a session stands, changing nothing."""
    loaded = read_capability_config()
    with open_database() as session:
        row = find_session(session, session_id)
        playbook = session_playbook(
            session, load_playbook(session, loaded, row.playbook_id), row
        )
        view = read_session_view(session, loaded, row, playbook)
        return build_session_response(session, view, loaded, [])


@router.post("/{session_id}/begin", response_model=SessionResponse)
def begin_admin_session(
    session_id: str,
    _: None = Depends(require_api_key),
) -> SessionResponse:
    """Start working the plan, from its first activity."""
    loaded = read_capability_config()
    with open_database() as session:
        row = find_session(session, session_id)
        playbook = session_playbook(
            session, load_playbook(session, loaded, row.playbook_id), row
        )
        try:
            view = begin_session(session, loaded, row, playbook)
        except SessionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return build_session_response(session, view, loaded, [])


@router.post("/{session_id}/activities/{activity_key}/begin", response_model=SessionResponse)
def begin_session_activity(
    session_id: str,
    activity_key: str,
    _: None = Depends(require_api_key),
) -> SessionResponse:
    """Start one activity by name, announcing what it covers before working it."""
    loaded = read_capability_config()
    with open_database() as session:
        row = find_session(session, session_id)
        playbook = session_playbook(
            session, load_playbook(session, loaded, row.playbook_id), row
        )
        try:
            view = begin_activity(session, loaded, row, playbook, activity_key)
        except SessionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SessionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return build_session_response(session, view, loaded, [])


@router.post("/{session_id}/advance", response_model=SessionResponse)
def advance_admin_session(
    session_id: str,
    _: None = Depends(require_api_key),
) -> SessionResponse:
    """Move on to the next activity, once the one in hand is finished with.

    Finished with is asked of the work rather than asserted: the email
    activity is done when its review is, and a review holding decisions the
    mailbox has not seen is not done.
    """
    loaded = read_capability_config()
    with open_database() as session:
        row = find_session(session, session_id)
        playbook = session_playbook(
            session, load_playbook(session, loaded, row.playbook_id), row
        )
        view = advance_session(session, loaded, row, playbook)
        return build_session_response(session, view, loaded, [])


async def sync_if_asked(
    loaded: LoadedCapabilities, request: StartSessionRequest, scope: ReviewScope
) -> EvidenceRefresh:
    if not request.sync:
        return EvidenceRefresh(warnings=[], read_at=None)
    return await refresh_evidence(loaded, request.limit, scope)


@contextmanager
def open_database() -> Iterator[Session]:
    try:
        with session_scope() as session:
            yield session
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def load_playbook(
    session: Session, loaded: LoadedCapabilities, playbook_id: str
) -> ActivePlaybook:
    try:
        return read_active_playbook(session, loaded, playbook_id)
    except (PlaybookError, RevisionRefused) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def find_session(session: Session, session_id: str) -> AssistantSession:
    try:
        return read_session(session, session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def build_session_response(
    session: Session,
    view: SessionView,
    loaded: LoadedCapabilities,
    warnings: list[str],
    opening: OpeningMode | None = None,
) -> SessionResponse:
    review = (
        build_run_response(view.review, [], loaded) if view.review is not None else None
    )
    current = view.current()
    closeout = (
        build_closeout(session, view)
        if current is not None and current.row.activity_key == CLOSEOUT_ACTIVITY
        else None
    )
    return SessionResponse(
        session_id=view.row.id,
        status=view.row.status,
        opened_at=view.row.opened_at,
        begun_at=view.row.begun_at,
        completed_at=view.row.completed_at,
        supersedes_session_id=view.row.supersedes_session_id,
        playbook=SessionPlaybookResponse(
            playbook_id=view.playbook.revision.playbook_id,
            name=view.playbook.document.name,
            revision=build_revision_response(view.playbook.revision),
            validation=build_validation_response(view.playbook.report),
        ),
        opening=build_opening(loaded, opening),
        plan=build_plan(view),
        prompt=build_prompt(view),
        review=review,
        closeout=closeout,
        warnings=warnings,
    )


def build_opening(
    loaded: LoadedCapabilities, opening: OpeningMode | None
) -> SessionOpeningResponse | None:
    """The playbook's own words, and only at the door.

    Returned by starting and continuing a session and by nothing else, so the
    orientation cannot be repeated between activities: it is not there to
    repeat.
    """
    if opening is None:
        return None
    text = loaded.opening.new if opening is OpeningMode.NEW else loaded.opening.resumed
    return SessionOpeningResponse(mode=opening, text=text)


def build_plan(view: SessionView) -> SessionPlanResponse:
    activities = [
        SessionActivityResponse(
            activity_key=activity.row.activity_key,
            label=activity.row.label,
            position=activity.row.position,
            state=activity.row.state,
            availability=activity.kind.availability,
            data_source=activity.kind.source,
            intro=activity.intro,
            steps=[
                SessionStepResponse(
                    key=step.key,
                    label=step.label,
                    position=step.position,
                    count=step.count,
                    state=step.state,
                )
                for step in activity.steps
            ],
            review_id=activity.row.run_id,
        )
        for activity in view.activities
    ]
    working = [
        activity.activity_key
        for activity in (item.row for item in view.activities)
        if activity.state
        not in {ActivityState.SKIPPED, ActivityState.UNAVAILABLE}
    ]
    current = view.current()
    return SessionPlanResponse(
        message=plan_message(view),
        activities=activities,
        working=working,
        skipped=[
            activity.row.activity_key
            for activity in view.activities
            if activity.row.state == ActivityState.SKIPPED
        ],
        unavailable=[
            activity.row.activity_key
            for activity in view.activities
            if activity.row.state == ActivityState.UNAVAILABLE
        ],
        current=current.row.activity_key if current is not None else None,
        activity_number=(
            working.index(current.row.activity_key) + 1
            if current is not None and current.row.activity_key in working
            else None
        ),
        activity_count=len(working),
        overrides=list(view.row.overrides or []),
    )


def plan_message(view: SessionView) -> str:
    """The plan in sentences: what this session does, in order, and what first.

    Written here rather than left to the caller for the same reason the
    opening is: a plan read out differently each morning is not a plan that
    can be checked against what then happens.
    """
    workable = view.workable()
    if not workable:
        return "There is nothing in this session's plan to work."

    said = ["Here's our plan for this session:"]
    said.append(sequence_sentence([activity.row.label for activity in workable]))

    unavailable = [
        activity.row.label
        for activity in view.activities
        if activity.row.state == ActivityState.UNAVAILABLE
    ]
    if unavailable:
        said.append(
            f"{join_labels(unavailable)} "
            + ("is" if len(unavailable) == 1 else "are")
            + " in the playbook and not built here yet, so we'll name "
            + ("it" if len(unavailable) == 1 else "them")
            + " and move on."
        )

    skipped = [
        activity.row.label
        for activity in view.activities
        if activity.row.state == ActivityState.SKIPPED
    ]
    if skipped:
        said.append(f"{join_labels(skipped)} is set aside for this session only.")

    first = view.current() or workable[0]
    said.append(f"We'll start with {first.row.label}.")
    if first.steps:
        said.append(
            f"Within {first.row.label}, we'll review "
            + join_labels([step.label for step in first.steps])
            + "."
        )
    return " ".join(said)


def sequence_sentence(labels: list[str]) -> str:
    """"First X. Then Y. After that Z. Finally W." — the order, said as an order."""
    openers = ["First", "Then", "After that", "Next"]
    parts: list[str] = []
    for position, label in enumerate(labels):
        if position == len(labels) - 1 and len(labels) > 1:
            parts.append(f"Finally, we'll review {label}.")
        else:
            opener = openers[min(position, len(openers) - 1)]
            parts.append(f"{opener}, we'll review {label}.")
    return " ".join(parts)


def join_labels(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def build_prompt(view: SessionView) -> SessionPromptResponse | None:
    """What Brian is being asked, and the exact requests that answer it."""
    if view.row.status == SessionStatus.PROPOSED:
        return SessionPromptResponse(
            message=(
                "Shall we begin? You can also reorder this session, skip an activity "
                "for today, or change the playbook for good."
            ),
            choices=[
                SessionChoiceResponse(
                    operation="beginSession",
                    label="Begin the session",
                    method="POST",
                    path=f"/session/{view.row.id}/begin",
                ),
                SessionChoiceResponse(
                    operation="proposePlaybookChange",
                    label="Change the playbook from now on",
                    method="POST",
                    path="/playbook/propose",
                ),
            ],
        )
    if view.row.status == SessionStatus.COMPLETED:
        return None

    current = view.current()
    if current is None:
        return None
    if current.row.state == ActivityState.UNAVAILABLE:
        return SessionPromptResponse(
            message=(
                f"{current.row.label} is in the playbook and isn't built here yet. "
                "We can move on to the next activity."
            ),
            choices=[
                SessionChoiceResponse(
                    operation="advanceSession",
                    label="Move to the next activity",
                    method="POST",
                    path=f"/session/{view.row.id}/advance",
                )
            ],
        )
    return None


def build_closeout(session: Session, view: SessionView) -> SessionCloseoutResponse:
    """The end of the session, counted rather than characterised."""
    summary = view.summary
    awaiting = 0
    if view.review is not None:
        awaiting = sum(group.standing.outstanding() for group in view.review.groups)

    proposed, activated = playbook_changes(session, view)
    completed = [
        activity.row.label
        for activity in view.activities
        if activity.row.state == ActivityState.COMPLETED
    ]
    return SessionCloseoutResponse(
        activities_completed=completed,
        activities_skipped=[
            activity.row.label
            for activity in view.activities
            if activity.row.state == ActivityState.SKIPPED
        ],
        activities_unavailable=[
            activity.row.label
            for activity in view.activities
            if activity.row.state == ActivityState.UNAVAILABLE
        ],
        items_reviewed=summary.reviewed if summary is not None else 0,
        actions_verified=dict(summary.done) if summary is not None else {},
        deferred=summary.deferred if summary is not None else 0,
        dismissed=summary.dismissed if summary is not None else 0,
        awaiting_execution=awaiting,
        playbook_changes_proposed=proposed,
        playbook_changes_activated=activated,
        message=closeout_message(summary, awaiting, completed),
    )


def playbook_changes(session: Session, view: SessionView) -> tuple[list[str], list[str]]:
    """Playbook revisions written during this session, proposed and in force.

    Reported at the close because a change agreed in passing is exactly the
    kind of thing worth seeing again at the end of the day, while it can still
    be taken back.
    """
    revisions = list(
        session.execute(
            select(PlaybookRevision)
            .where(
                PlaybookRevision.playbook_id == view.row.playbook_id,
                PlaybookRevision.created_at >= view.row.opened_at,
            )
            .order_by(PlaybookRevision.number)
        )
        .scalars()
        .all()
    )
    proposed = [
        sentence
        for revision in revisions
        if revision.status == RevisionStatus.PROPOSED
        for sentence in (revision.change_summary or [])
    ]
    activated = [
        sentence
        for revision in revisions
        if revision.status == RevisionStatus.ACTIVE
        for sentence in (revision.change_summary or [])
    ]
    return proposed, activated


def closeout_message(
    summary: ReviewSummary | None, awaiting: int, completed: list[str]
) -> str:
    reviewed = summary.reviewed if summary is not None else 0
    said = (
        f"{len(completed)} "
        + ("activity" if len(completed) == 1 else "activities")
        + f" completed; {reviewed} items reviewed."
    )
    if summary is not None and summary.done:
        said += " " + "; ".join(
            f"{count} {name}" for name, count in sorted(summary.done.items())
        ) + ". Counted from verified execution."
    if awaiting:
        said += (
            f" {awaiting} "
            + ("row is" if awaiting == 1 else "rows are")
            + " decided and the mailbox has not seen "
            + ("it" if awaiting == 1 else "them")
            + " yet, so this session is not finished with."
        )
    return said
