from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime

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
from adminos.config import get_gmail_credentials
from adminos.db.engine import DatabaseNotConfigured, session_scope
from adminos.db.models import JsonObject, ReviewGroup, ReviewItem, ReviewRun
from adminos.domain.evidence import (
    DEFAULT_SYNC_LIMIT,
    MAX_SYNC_LIMIT,
    PruneScanTruncated,
    sync_gmail_evidence,
)
from adminos.domain.presentation import ScreenView, render_group
from adminos.domain.review import (
    Assessment,
    BulkDecisionRefused,
    DecisionKind,
    DecisionRefused,
    GroupView,
    ReviewNotFound,
    RunView,
    decide_group,
    read_group,
    read_item,
    read_run,
    record_assessment,
    record_decision,
    refresh_states,
    start_or_resume_review,
)
from adminos.logging import get_logger


logger = get_logger(__name__)
router = APIRouter(prefix="/review", tags=["review"])


class StartReviewRequest(BaseModel):
    review_date: date | None = None
    sync: bool = True
    limit: int = Field(default=DEFAULT_SYNC_LIMIT, ge=1, le=MAX_SYNC_LIMIT)


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
    category: str | None
    objectives: list[str]
    approved_action: str | None
    requires_confirmation: bool


class ColumnResponse(BaseModel):
    key: str
    label: str
    align: str
    format: str


class ScreenActionResponse(BaseModel):
    id: str
    label: str
    decision: str
    action: str | None
    scope: str
    method: str
    path: str
    body: dict[str, str]


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
    screen: ScreenResponse
    items: list[ItemResponse]


class GroupSummaryResponse(BaseModel):
    capability_key: str
    capability_name: str
    position: int
    state: str
    counts: dict[str, int]


class RunResponse(BaseModel):
    run_id: str
    review_date: date
    channel: str
    state: str
    config_version: str
    config_digest: str
    screen_id: str | None
    groups: list[GroupSummaryResponse]
    current_group: GroupResponse | None
    warnings: list[str]


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
    """Start or resume today's review, and return the first group to work.

    One call answers "good morning": it refreshes the mailbox, builds or
    resumes the run, and hands back a single capability group rather than an
    undifferentiated inbox.
    """
    loaded = read_capability_config()
    warnings: list[str] = []

    if request.sync:
        warnings.extend(await refresh_evidence(loaded, request.limit))

    try:
        with session_scope() as session:
            view = start_or_resume_review(session, loaded, review_date=request.review_date)
            return build_run_response(view, warnings, loaded)
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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


async def refresh_evidence(loaded: LoadedCapabilities, limit: int) -> list[str]:
    """Pull new mail before the review runs. Never fatal to the review itself."""
    credentials = get_gmail_credentials()
    if credentials is None:
        return ["Gmail is not configured, so the review shows only evidence already recorded."]

    try:
        async with open_gmail_client(credentials) as client:
            with session_scope() as session:
                result = await sync_gmail_evidence(
                    client,
                    session,
                    loaded.enabled(),
                    limit=limit,
                )
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PruneScanTruncated as exc:  # pragma: no cover - prune is never requested here
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GmailError as exc:
        logger.error("review sync failed: %s", type(exc).__name__)
        return [f"Gmail could not be read, so the review may be out of date: {exc}"]

    return result.warnings


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


def build_run_response(
    view: RunView,
    warnings: list[str],
    loaded: LoadedCapabilities,
) -> RunResponse:
    current = view.current_group()
    rendered = build_group_response(current, loaded, view.run) if current else None
    return RunResponse(
        run_id=view.run.id,
        review_date=view.run.review_date,
        channel=view.run.channel,
        state=view.run.state,
        config_version=view.run.config_version,
        config_digest=view.run.config_digest,
        screen_id=rendered.screen_id if rendered else None,
        groups=[
            GroupSummaryResponse(
                capability_key=group.group.capability_key,
                capability_name=group.group.capability_name,
                position=group.group.position,
                state=group.group.state,
                counts=count_states(group.items),
            )
            for group in view.groups
        ],
        current_group=rendered,
        warnings=warnings,
    )


def build_group_response(
    view: GroupView,
    loaded: LoadedCapabilities,
    run: ReviewRun,
) -> GroupResponse:
    screen = build_screen_response(view, loaded, run)
    return GroupResponse(
        capability_key=view.group.capability_key,
        capability_name=view.group.capability_name,
        position=view.group.position,
        state=view.group.state,
        policy_version=view.group.policy_version,
        screen_id=screen.screen_id,
        allowed_actions=[action.value for action in view.capability.allowed_actions],
        allow_bulk_decisions=view.capability.approval.allow_bulk_decisions,
        counts=count_states(view.items),
        screen=screen,
        items=[build_item_response(item, view.capability) for item in view.items],
    )


def build_screen_response(
    view: GroupView,
    loaded: LoadedCapabilities,
    run: ReviewRun,
) -> ScreenResponse:
    """Render the capability's presentation contract for this group."""
    try:
        screen = loaded.screen_for(view.capability)
    except UnknownCapability as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return as_screen_response(render_group(screen, view, run))


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
