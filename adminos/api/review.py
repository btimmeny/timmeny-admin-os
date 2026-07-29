from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from adminos.adapters.gmail import GmailError, open_gmail_client
from adminos.api.deps import read_capability, read_capability_config
from adminos.api.security import require_api_key
from adminos.capabilities.config import (
    ACTION_ALIASES,
    ACTION_VALUES,
    ActionKind,
    CapabilityConfig,
    LoadedCapabilities,
    UnknownCapability,
    read_action_kind,
)
from adminos.capabilities.screens import ScreenConfig
from adminos.config import get_gmail_credentials
from adminos.db.engine import DatabaseNotConfigured, session_scope
from adminos.db.models import JsonObject, ReviewGroup, ReviewItem, ReviewRun
from adminos.domain.evidence import (
    DEFAULT_SYNC_LIMIT,
    MAX_SYNC_LIMIT,
    PruneScanTruncated,
    sync_gmail_evidence,
)
from adminos.domain.mailboxes import (
    DEFAULT_SCOPE,
    SCOPE_NAMES,
    ReviewScope,
    UnknownScope,
    capability_scope,
    read_scope,
    read_stored_scope,
)
from adminos.domain.presentation import ScreenView, render_group, shown_items
from adminos.domain.review import (
    Assessment,
    BulkDecisionRefused,
    DecisionKind,
    DecisionRefused,
    GroupView,
    ItemState,
    ReviewClosed,
    ReviewNotFound,
    RunState,
    RunView,
    continue_review,
    decide_group,
    read_group,
    read_item,
    read_run,
    record_assessment,
    record_decision,
    refresh_states,
    restart_review,
    start_or_resume_review,
)
from adminos.logging import get_logger


logger = get_logger(__name__)
router = APIRouter(prefix="/review", tags=["review"])


class StartReviewRequest(BaseModel):
    review_date: date | None = None
    sync: bool = True
    limit: int = Field(default=DEFAULT_SYNC_LIMIT, ge=1, le=MAX_SYNC_LIMIT)
    scope: str | None = Field(
        default=None,
        description=(
            "Which mail to review. Omit it for the default, which is the inbox: "
            f"actionable mail only. Naming one of {SCOPE_NAMES} reviews that "
            "instead, and only ever because it was asked for."
        ),
    )


@dataclass(frozen=True, slots=True)
class EvidenceRefresh:
    """What a sync produced, and whether the mailbox was read at all.

    `read_at` is set only where Gmail actually answered, so a review records a
    refresh it had rather than one it attempted: an unconfigured or unreachable
    mailbox leaves the review's freshness where it was.
    """

    warnings: list[str]
    read_at: datetime | None


class ReviewScopeResponse(BaseModel):
    """What was reviewed, stated rather than left to be inferred.

    Returned on every review response so that "did you look at my archive?" is
    answered from the run itself. Nothing here is a preference or a learned
    rule: it is the query the review was built from.
    """

    name: str
    mailbox: str
    include_snoozed: bool
    include_archived: bool
    include_trash: bool
    include_spam: bool
    include_sent: bool
    include_drafts: bool
    requested: bool
    gmail_query: str
    description: str


def build_scope_response(scope: ReviewScope) -> ReviewScopeResponse:
    return ReviewScopeResponse(
        name=scope.name,
        mailbox=scope.mailbox.value,
        include_snoozed=scope.include_snoozed,
        include_archived=scope.include_archived,
        include_trash=scope.include_trash,
        include_spam=scope.include_spam,
        include_sent=scope.include_sent,
        include_drafts=scope.include_drafts,
        requested=scope.requested,
        gmail_query=scope.query(),
        description=scope.describes(),
    )


class ItemResponse(BaseModel):
    item_id: str
    evidence_id: str
    thread_id: str
    subject: str | None
    participants: list[str]
    received_at: datetime | None
    state: str
    recommendation: str
    recommendation_source: str
    recommendation_confidence: float
    recommendation_rationale: str | None
    recommendation_params: JsonObject | None
    category: str | None
    objectives: list[str]
    approved_action: str | None
    requires_confirmation: bool


class ColumnResponse(BaseModel):
    key: str
    label: str
    align: str
    format: str


class ScreenParamResponse(BaseModel):
    """A value the action needs, and the only values it accepts."""

    name: str
    label: str
    required: bool
    choices: list[str]


class ScreenActionResponse(BaseModel):
    id: str
    label: str
    decision: str
    action: str | None
    scope: str
    method: str
    path: str
    body: dict[str, str]
    params: list[ScreenParamResponse] = []


class RowResponse(BaseModel):
    item_id: str
    thread_id: str
    cells: list[str]
    actions: list[str]


class ScreenResponse(BaseModel):
    """The presentation contract, and the rows it describes.

    `cells` are finished strings in `columns` order: a renderer prints them and
    nothing else. Every layout decision — wording, ordering, formatting,
    truncation, which decisions may be offered — was made here.
    """

    screen_id: str
    kind: str
    title: str
    columns: list[ColumnResponse]
    actions: list[ScreenActionResponse]
    rows: list[RowResponse]
    footer: str
    empty_text: str


class RestorableItemResponse(BaseModel):
    """A thread that was moved to Trash and can be taken back out of it.

    Listed separately because the table shows what still needs deciding, and a
    trashed thread does not. It carries the exact request that restores it, so
    undoing a Trash never depends on anyone reconstructing one.
    """

    item_id: str
    thread_id: str
    subject: str | None
    trashed_at: datetime | None
    action: str
    method: str
    path: str
    body: dict[str, str]


class GroupResponse(BaseModel):
    capability_key: str
    capability_name: str
    position: int
    state: str
    policy_version: str
    screen_id: str
    allowed_actions: list[str]
    allow_bulk_decisions: bool
    counts: dict[str, int]
    scope: ReviewScopeResponse
    screen: ScreenResponse
    items: list[ItemResponse]
    restorable: list[RestorableItemResponse] = Field(default_factory=list)


class GroupSummaryResponse(BaseModel):
    capability_key: str
    capability_name: str
    position: int
    state: str
    counts: dict[str, int]


class ReviewChoiceResponse(BaseModel):
    """One thing that can be done next, with the request that does it."""

    operation: str
    label: str
    method: str
    path: str
    body: JsonObject


class ReviewPromptResponse(BaseModel):
    """A question for Brian that Admin OS will not answer on his behalf.

    Returned instead of a resumed review when today's is already finished:
    what happens next is a choice between leaving it as it stands and
    reviewing the day again on refreshed mail, and both of those are his to
    make. The choices carry their own requests, so nothing has to be guessed.
    """

    reason: str
    message: str
    choices: list[ReviewChoiceResponse]


class RunResponse(BaseModel):
    review_id: str
    run_id: str
    """The same identifier as `review_id`, kept for callers written before a
    review had a name of its own."""
    review_date: date
    revision: int
    channel: str
    scope: ReviewScopeResponse
    status: str
    state: str
    """The same value as `status`, under the older name."""
    started_at: datetime | None
    completed_at: datetime | None
    abandoned_at: datetime | None
    evidence_refresh_at: datetime | None
    config_version: str
    config_digest: str
    screen_id: str | None
    groups: list[GroupSummaryResponse]
    current_group: GroupResponse | None
    prompt: ReviewPromptResponse | None = None
    warnings: list[str]


RESTORE_ACTION = "restore_gmail_thread_from_trash"
"""The spoken name of the one decision a settled item still accepts."""

ACTION_DESCRIPTION = (
    "The action to take, by its stored name or its spoken one: "
    f"{', '.join(sorted(ACTION_ALIASES))}."
)


def read_requested_action(value: object) -> object:
    """Accept a spoken action name where a stored one is expected."""
    if isinstance(value, str):
        try:
            return read_action_kind(value)
        except ValueError:
            return value
    return value


class DecisionRequest(BaseModel):
    decision: DecisionKind
    action: ActionKind | None = Field(default=None, description=ACTION_DESCRIPTION)
    action_params: JsonObject | None = None
    note: str | None = None

    _read_action = field_validator("action", mode="before")(read_requested_action)


class BulkDecisionRequest(BaseModel):
    decision: DecisionKind
    item_ids: list[str] | None = None
    action: ActionKind | None = Field(default=None, description=ACTION_DESCRIPTION)
    action_params: JsonObject | None = None
    note: str | None = None

    _read_action = field_validator("action", mode="before")(read_requested_action)


class IneligibleItemResponse(BaseModel):
    item_id: str
    thread_id: str
    subject: str | None
    reason: str


class BulkRefusalResponse(BaseModel):
    """Why a bulk decision was refused, row by row. Nothing was recorded."""

    message: str
    ineligible: list[IneligibleItemResponse]


class AssessmentRequest(BaseModel):
    """What the model is allowed to say about a thread.

    Constrained on purpose: a category the capability recognises, a bounded
    confidence, and at most a suggestion. Workflow state is not settable here.
    """

    category: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    model_version: str
    recommendation: str | None = None


class DecisionResponse(BaseModel):
    run: RunResponse
    decided: list[ItemResponse]


@router.post("/start", response_model=RunResponse)
async def start_review(
    request: StartReviewRequest,
    _: None = Depends(require_api_key),
) -> RunResponse:
    """Start today's review, or resume the one still under way.

    One call answers "good morning": it refreshes the mailbox, builds or
    resumes the review, and hands back a single capability group rather than
    an undifferentiated inbox.

    A review already finished is not reopened. Mail that arrived since would
    otherwise turn a morning's completed work back into an unfinished list, so
    the finished review comes back with the choice — leave it, or review the
    day again on refreshed mail — and the choice is Brian's.

    The review is of the inbox unless another scope is named. That is not a
    preference to be confirmed or a rule to be learned; it is the query, and
    the scope that was used comes back in the response.
    """
    loaded = read_capability_config()
    scope = requested_scope(request.scope)
    refresh = await sync_if_asked(loaded, request, scope)

    try:
        with session_scope() as session:
            try:
                view = start_or_resume_review(
                    session,
                    loaded,
                    review_date=request.review_date,
                    scope=scope,
                    evidence_refresh_at=refresh.read_at,
                )
            except ReviewClosed as exc:
                return build_closed_response(session, loaded, exc.run, request, refresh.warnings)
            return build_run_response(view, refresh.warnings, loaded)
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/continue", response_model=RunResponse)
async def continue_daily_review(
    request: StartReviewRequest,
    _: None = Depends(require_api_key),
) -> RunResponse:
    """Pick up the review under way, and never open a new one.

    "Where was I?" and "start my review" are different questions, and this one
    has an honest answer when there is nothing to resume: a review that does
    not exist, or one already finished with, is reported rather than created.
    """
    loaded = read_capability_config()
    scope = requested_scope(request.scope)
    refresh = await sync_if_asked(loaded, request, scope)

    try:
        with session_scope() as session:
            try:
                view = continue_review(
                    session,
                    loaded,
                    review_date=request.review_date,
                    scope=scope,
                    evidence_refresh_at=refresh.read_at,
                )
            except ReviewNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ReviewClosed as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "ReviewClosed",
                        "review_id": exc.run.id,
                        "status": exc.run.state,
                        "message": str(exc),
                    },
                ) from exc
            return build_run_response(view, refresh.warnings, loaded)
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/restart", response_model=RunResponse)
async def restart_daily_review(
    request: StartReviewRequest,
    _: None = Depends(require_api_key),
) -> RunResponse:
    """Put today's review aside and open a fresh one on refreshed mail.

    The review being replaced is abandoned rather than deleted: its decisions
    and the actions they ran are what happened, and they stay readable. What
    it loses is any preparation still open in it, which stops being executable
    the moment it is set aside.
    """
    loaded = read_capability_config()
    scope = requested_scope(request.scope)
    refresh = await sync_if_asked(loaded, request, scope)

    try:
        with session_scope() as session:
            view = restart_review(
                session,
                loaded,
                review_date=request.review_date,
                scope=scope,
                evidence_refresh_at=refresh.read_at,
            )
            return build_run_response(view, refresh.warnings, loaded)
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def requested_scope(name: str | None) -> ReviewScope:
    try:
        return read_scope(name)
    except UnknownScope as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def sync_if_asked(
    loaded: LoadedCapabilities,
    request: StartReviewRequest,
    scope: ReviewScope,
) -> EvidenceRefresh:
    if not request.sync:
        return EvidenceRefresh(warnings=[], read_at=None)
    return await refresh_evidence(loaded, request.limit, scope)


@router.get("/runs/{run_id}", response_model=RunResponse)
def read_review_run(run_id: str, _: None = Depends(require_api_key)) -> RunResponse:
    """Report where a review stands, without changing anything."""
    loaded = read_capability_config()
    with open_review(run_id) as (session, run):
        return build_run_response(refresh_states(session, loaded, run), [], loaded)


@router.get("/runs/{run_id}/groups/{capability_key}", response_model=GroupResponse)
def read_review_group(
    run_id: str,
    capability_key: str,
    _: None = Depends(require_api_key),
) -> GroupResponse:
    """Return one capability group, the unit the review is presented in."""
    loaded = read_capability_config()
    read_capability(loaded, capability_key)

    with open_review(run_id) as (session, run):
        lookup_group(session, run, capability_key)
        view = refresh_states(session, loaded, run)
        for group in view.groups:
            if group.group.capability_key == capability_key:
                return build_group_response(group, loaded, run)
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} has no {capability_key!r}.")


@router.post("/runs/{run_id}/items/{item_id}/decision", response_model=DecisionResponse)
def decide_item(
    run_id: str,
    item_id: str,
    request: DecisionRequest,
    _: None = Depends(require_api_key),
) -> DecisionResponse:
    """Record one decision. Approval is the only route to an action."""
    loaded = read_capability_config()

    with open_review(run_id) as (session, run):
        refuse_abandoned(run)
        item = lookup_item(session, run, item_id)
        group = session.get(ReviewGroup, item.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail=f"Item {item_id!r} has no group.")
        capability = read_capability(loaded, group.capability_key)

        try:
            decided = record_decision(
                session,
                capability,
                run,
                item,
                request.decision,
                action=request.action,
                action_params=request.action_params,
                note=request.note,
            )
        except DecisionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        view = refresh_states(session, loaded, run)
        return DecisionResponse(
            run=build_run_response(view, [], loaded),
            decided=[build_item_response(decided, capability)],
        )


@router.post("/runs/{run_id}/groups/{capability_key}/decisions", response_model=DecisionResponse)
def decide_items(
    run_id: str,
    capability_key: str,
    request: BulkDecisionRequest,
    _: None = Depends(require_api_key),
) -> DecisionResponse:
    """Apply one decision across a group: "archive all of these".

    All or nothing. If the decision is not permitted for one item, none of them
    is decided, so a bulk answer cannot partly land.
    """
    loaded = read_capability_config()
    capability = read_capability(loaded, capability_key)

    with open_review(run_id) as (session, run):
        refuse_abandoned(run)
        group = lookup_group(session, run, capability_key)
        try:
            decided = decide_group(
                session,
                capability,
                run,
                group,
                request.decision,
                item_ids=request.item_ids,
                action=request.action,
                action_params=request.action_params,
                note=request.note,
                batch_id=group.id,
            )
        except BulkDecisionRefused as exc:
            raise HTTPException(
                status_code=409,
                detail=BulkRefusalResponse(
                    message=str(exc),
                    ineligible=[
                        IneligibleItemResponse(
                            item_id=entry.item_id,
                            thread_id=entry.thread_id,
                            subject=entry.subject,
                            reason=entry.reason,
                        )
                        for entry in exc.ineligible
                    ],
                ).model_dump(),
            ) from exc
        except DecisionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ReviewNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        view = refresh_states(session, loaded, run)
        return DecisionResponse(
            run=build_run_response(view, [], loaded),
            decided=[build_item_response(item, capability) for item in decided],
        )


@router.post("/runs/{run_id}/items/{item_id}/assessment", response_model=ItemResponse)
def assess_item(
    run_id: str,
    item_id: str,
    request: AssessmentRequest,
    _: None = Depends(require_api_key),
) -> ItemResponse:
    """Record the model's reading of a thread, inside the capability's limits.

    An assessment can sharpen what a thread is and suggest what to do with it.
    It cannot decide, approve, or execute anything.
    """
    loaded = read_capability_config()

    with open_review(run_id) as (session, run):
        refuse_abandoned(run)
        item = lookup_item(session, run, item_id)
        group = session.get(ReviewGroup, item.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail=f"Item {item_id!r} has no group.")
        capability = read_capability(loaded, group.capability_key)

        try:
            assessed = record_assessment(
                session,
                capability,
                item,
                Assessment(
                    category=request.category,
                    confidence=request.confidence,
                    rationale=request.rationale,
                    model_version=request.model_version,
                    recommendation=request.recommendation,
                ),
            )
        except DecisionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return build_item_response(assessed, capability)


async def refresh_evidence(
    loaded: LoadedCapabilities,
    limit: int,
    scope: ReviewScope = DEFAULT_SCOPE,
) -> EvidenceRefresh:
    """Pull new mail before the review runs. Never fatal to the review itself."""
    credentials = get_gmail_credentials()
    if credentials is None:
        return EvidenceRefresh(
            warnings=[
                "Gmail is not configured, so the review shows only evidence already recorded."
            ],
            read_at=None,
        )

    try:
        async with open_gmail_client(credentials) as client:
            with session_scope() as session:
                result = await sync_gmail_evidence(
                    client,
                    session,
                    loaded.enabled(),
                    scope=scope,
                    limit=limit,
                )
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PruneScanTruncated as exc:  # pragma: no cover - prune is never requested here
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GmailError as exc:
        logger.error("review sync failed: %s", type(exc).__name__)
        return EvidenceRefresh(
            warnings=[f"Gmail could not be read, so the review may be out of date: {exc}"],
            read_at=None,
        )

    return EvidenceRefresh(warnings=result.warnings, read_at=datetime.now(UTC))


@contextmanager
def open_review(run_id: str) -> Iterator[tuple[Session, ReviewRun]]:
    """Open a session and load the run, translating absence into 404."""
    try:
        with session_scope() as session:
            try:
                run = read_run(session, run_id)
            except ReviewNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            yield session, run
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def refuse_abandoned(run: ReviewRun) -> None:
    """Nothing is decided, prepared, or executed in a review that was set aside.

    The review that was restarted still exists and still reads back, which is
    the point of abandoning rather than deleting it. What it must not do is
    accept work: a decision made in yesterday's abandoned review, or a scope
    prepared before a restart, would act on a table nobody is looking at.
    """
    if RunState(run.state) is not RunState.ABANDONED:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "error": "ReviewAbandoned",
            "review_id": run.id,
            "status": run.state,
            "message": (
                f"Review {run.id} was abandoned, so it takes no further "
                "decisions or actions. Work in the current review instead."
            ),
        },
    )


def lookup_group(session: Session, run: ReviewRun, capability_key: str) -> ReviewGroup:
    try:
        return read_group(session, run, capability_key)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def lookup_item(session: Session, run: ReviewRun, item_id: str) -> ReviewItem:
    try:
        return read_item(session, run, item_id)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def requires_confirmation(capability: CapabilityConfig, item: ReviewItem) -> bool:
    """Whether this item needs a human before anything happens to it.

    True unless the capability auto-approves exactly this action at this
    confidence, which no capability currently does.
    """
    if item.recommendation not in ACTION_VALUES:
        return True
    return not capability.auto_approves(
        ActionKind(item.recommendation), item.recommendation_confidence
    )


def build_closed_response(
    session: Session,
    loaded: LoadedCapabilities,
    run: ReviewRun,
    request: StartReviewRequest,
    warnings: list[str],
) -> RunResponse:
    """Report the finished review, and offer the two honest ways on."""
    body: JsonObject = {"sync": True, "limit": request.limit}
    if run.scope_name != DEFAULT_SCOPE.name:
        body["scope"] = run.scope_name
    return build_run_response(
        refresh_states(session, loaded, run),
        warnings,
        loaded,
        prompt=ReviewPromptResponse(
            reason=f"review_{run.state}",
            message=(
                f"The {run.scope_name} review for {run.review_date.isoformat()} is "
                f"already {run.state}. Nothing has been reopened."
            ),
            choices=[
                ReviewChoiceResponse(
                    operation="readDailyReview",
                    label="Leave it as it stands, and read what was decided",
                    method="GET",
                    path=f"/review/runs/{run.id}",
                    body={},
                ),
                ReviewChoiceResponse(
                    operation="restartDailyReview",
                    label="Review the day again, on refreshed mail",
                    method="POST",
                    path="/review/restart",
                    body=body,
                ),
            ],
        ),
    )


def build_run_response(
    view: RunView,
    warnings: list[str],
    loaded: LoadedCapabilities,
    prompt: ReviewPromptResponse | None = None,
) -> RunResponse:
    scope = read_stored_scope(view.run.scope)
    current = view.current_group()
    rendered = build_group_response(current, loaded, view.run) if current else None
    return RunResponse(
        review_id=view.run.id,
        run_id=view.run.id,
        review_date=view.run.review_date,
        revision=view.run.revision,
        channel=view.run.channel,
        scope=build_scope_response(scope),
        status=view.run.state,
        state=view.run.state,
        started_at=view.run.started_at,
        completed_at=view.run.completed_at,
        abandoned_at=view.run.abandoned_at,
        evidence_refresh_at=view.run.evidence_refresh_at,
        config_version=view.run.config_version,
        config_digest=view.run.config_digest,
        screen_id=rendered.screen_id if rendered else None,
        groups=[
            GroupSummaryResponse(
                capability_key=group.group.capability_key,
                capability_name=group.group.capability_name,
                position=group.group.position,
                state=group.group.state,
                counts=group_counts(group, loaded),
            )
            for group in view.groups
        ],
        current_group=rendered,
        prompt=prompt,
        warnings=warnings,
    )


def build_group_response(
    view: GroupView,
    loaded: LoadedCapabilities,
    run: ReviewRun,
) -> GroupResponse:
    screen = build_screen_response(view, loaded, run)
    scope = capability_scope(read_stored_scope(run.scope), view.capability)
    return GroupResponse(
        capability_key=view.group.capability_key,
        capability_name=view.group.capability_name,
        position=view.group.position,
        state=view.group.state,
        policy_version=view.group.policy_version,
        screen_id=screen.screen_id,
        allowed_actions=[action.value for action in view.capability.allowed_actions],
        allow_bulk_decisions=view.capability.approval.allow_bulk_decisions,
        counts=count_states(view.items) | {"remaining": len(screen.rows)},
        scope=build_scope_response(scope),
        screen=screen,
        items=[build_item_response(item, view.capability) for item in view.items],
        restorable=build_restorable(view, run),
    )


def build_restorable(view: GroupView, run: ReviewRun) -> list[RestorableItemResponse]:
    """The trashed threads this capability may take back out of Trash.

    Empty unless the capability is granted `gmail.untrash`: what can be undone
    is a permission like any other, not an assumption.
    """
    if not view.capability.permits(ActionKind.GMAIL_UNTRASH):
        return []
    return [
        RestorableItemResponse(
            item_id=item.id,
            thread_id=item.source_thread_id,
            subject=item.subject,
            trashed_at=item.decided_at,
            action=RESTORE_ACTION,
            method="POST",
            path=f"/review/runs/{run.id}/items/{item.id}/decision",
            body={"decision": DecisionKind.OVERRIDE.value, "action": RESTORE_ACTION},
        )
        for item in view.items
        if item.state == ItemState.EXECUTED
        and item.approved_action == ActionKind.GMAIL_TRASH.value
    ]


def build_screen_response(
    view: GroupView,
    loaded: LoadedCapabilities,
    run: ReviewRun,
) -> ScreenResponse:
    """Render the capability's presentation contract for this group."""
    return as_screen_response(render_group(lookup_screen(view, loaded), view, run))


def as_screen_response(rendered: ScreenView) -> ScreenResponse:
    return ScreenResponse(
        screen_id=rendered.screen_id,
        kind=rendered.kind,
        title=rendered.title,
        columns=[
            ColumnResponse(
                key=column.key,
                label=column.label,
                align=column.align,
                format=column.format,
            )
            for column in rendered.columns
        ],
        actions=[
            ScreenActionResponse(
                id=action.id,
                label=action.label,
                decision=action.decision,
                action=action.action,
                scope=action.scope,
                method=action.method,
                path=action.path,
                body=action.body,
                params=[
                    ScreenParamResponse(
                        name=param.name,
                        label=param.label,
                        required=param.required,
                        choices=param.choices,
                    )
                    for param in action.params
                ],
            )
            for action in rendered.actions
        ],
        rows=[
            RowResponse(
                item_id=row.item_id,
                thread_id=row.thread_id,
                cells=row.cells,
                actions=row.actions,
            )
            for row in rendered.rows
        ],
        footer=rendered.footer,
        empty_text=rendered.empty_text,
    )


def build_item_response(item: ReviewItem, capability: CapabilityConfig) -> ItemResponse:
    return ItemResponse(
        item_id=item.id,
        evidence_id=item.evidence_id,
        thread_id=item.source_thread_id,
        subject=item.subject,
        participants=[value for value in (item.participants or []) if isinstance(value, str)],
        received_at=item.received_at,
        state=item.state,
        recommendation=item.recommendation,
        recommendation_source=item.recommendation_source,
        recommendation_confidence=item.recommendation_confidence,
        recommendation_rationale=item.recommendation_rationale,
        recommendation_params=item.recommendation_params,
        category=item.category,
        objectives=[value for value in (item.objective_keys or []) if isinstance(value, str)],
        approved_action=item.approved_action,
        requires_confirmation=requires_confirmation(capability, item),
    )


def count_states(items: list[ReviewItem]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(items)}
    for item in items:
        counts[item.state] = counts.get(item.state, 0) + 1
    return counts


def group_counts(view: GroupView, loaded: LoadedCapabilities) -> dict[str, int]:
    """How this group stands: every item by state, and how many still want one.

    `remaining` is the progress number. `total` is history — how many threads
    the capability has held today, including the ones already dealt with — and
    saying "4 of 28" reads the second as the first.
    """
    screen = lookup_screen(view, loaded)
    return count_states(view.items) | {"remaining": len(shown_items(screen, view.items))}


def lookup_screen(view: GroupView, loaded: LoadedCapabilities) -> ScreenConfig:
    try:
        return loaded.screen_for(view.capability)
    except UnknownCapability as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
