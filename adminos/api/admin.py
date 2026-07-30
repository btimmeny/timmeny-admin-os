from datetime import datetime
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from adminos.adapters.gmail import GmailAuthError, GmailError, open_gmail_client
from adminos.adapters.monday import (
    ItemFilter,
    MondayAuthError,
    MondayError,
    MondayItem,
    open_monday_client,
    open_monday_writer,
)
from adminos.api.deps import read_capability_config
from adminos.api.security import require_api_key
from adminos.capabilities.config import CapabilityConfig
from adminos.config import (
    get_database_url,
    get_gmail_credentials,
    get_monday_token,
    get_todo_board_id,
    get_todo_group_id,
    is_gmail_write_enabled,
    redact_database_url,
)
from adminos.db import engine
from adminos.db.engine import DatabaseNotConfigured, session_scope
from adminos.domain.boards import (
    DEFAULT_ITEM_LIMIT,
    BoardScopeUnresolved,
    read_scoped_items,
    resolve_board_scope,
)
from adminos.domain.classification import classify_evidence, read_review_queue
from adminos.domain.duplicates import (
    CANDIDATE_SCORE,
    DEFAULT_MATCH_LIMIT,
    STRONG_MATCH_SCORE,
    DuplicateReport,
    find_duplicates,
)
from adminos.domain.evidence import (
    DEFAULT_SYNC_LIMIT,
    MAX_SYNC_LIMIT,
    PruneScanTruncated,
    sync_gmail_evidence,
)
from adminos.domain.playbook_store import read_active_playbook
from adminos.domain.tasks import (
    EvidenceNotFound,
    TaskCreationRefused,
    VerificationFailed,
    create_task_from_evidence,
)
from adminos.logging import get_logger


DEFAULT_REVIEW_LIMIT = 50
MAX_REVIEW_LIMIT = 200
DEFAULT_BOARD_LIMIT = 200
MAX_BOARD_LIMIT = 1500
MAX_DUPLICATE_TITLES = 20

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


class DatabaseStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    OK = "ok"
    ERROR = "error"


class DatabaseStatusResponse(BaseModel):
    status: DatabaseStatus
    revision: str | None = None
    detail: str | None = None


@router.get("/db-status", response_model=DatabaseStatusResponse)
def read_database_status(_: None = Depends(require_api_key)) -> DatabaseStatusResponse:
    """Report whether the operational database is reachable and migrated."""
    database_url = get_database_url()
    if database_url is None:
        return DatabaseStatusResponse(
            status=DatabaseStatus.NOT_CONFIGURED,
            detail="DATABASE_URL is not set; Admin OS is running without persistence.",
        )

    try:
        revision = read_current_revision()
    except SQLAlchemyError as exc:
        logger.error(
            "database status check failed for %s: %s",
            redact_database_url(database_url),
            type(exc).__name__,
        )
        return DatabaseStatusResponse(
            status=DatabaseStatus.ERROR,
            detail="Could not query the database. See service logs.",
        )

    if revision is None:
        return DatabaseStatusResponse(
            status=DatabaseStatus.ERROR,
            detail=(
                "Database is reachable but no migrations have been applied. "
                "Run 'alembic upgrade head'."
            ),
        )

    return DatabaseStatusResponse(status=DatabaseStatus.OK, revision=revision)


class CapabilityLabelResponse(BaseModel):
    capability_key: str
    label: str
    found: bool | None = None


class GmailStatusResponse(BaseModel):
    configured: bool
    write_enabled: bool
    labels: list[CapabilityLabelResponse]
    detail: str | None = None


class GmailSyncResponse(BaseModel):
    scope: str
    """Where the scan looked, so an empty result can be told from a narrow one."""
    labels: list[str]
    scanned: int
    created: int
    updated: int
    unchanged: int
    removed: int
    warnings: list[str]


class CapabilityResponse(BaseModel):
    key: str
    name: str
    enabled: bool
    position: int
    description: str | None
    labels: list[str]
    playbook: str
    playbook_steps: list[str]
    policy_version: str
    categories: list[str]
    allowed_actions: list[str]
    auto_approve: list[str]
    objectives: list[str]


class CapabilitiesResponse(BaseModel):
    version: str
    digest: str
    channel: str
    capabilities: list[CapabilityResponse]


@router.get("/capabilities", response_model=CapabilitiesResponse)
def read_capabilities(_: None = Depends(require_api_key)) -> CapabilitiesResponse:
    """Report the configuration the review engine is running on."""
    loaded = read_capability_config()
    return CapabilitiesResponse(
        version=loaded.version,
        digest=loaded.digest,
        channel=loaded.channel,
        capabilities=[describe_capability(capability) for capability in loaded.capabilities],
    )


@router.get("/gmail/status", response_model=GmailStatusResponse)
async def read_gmail_status(_: None = Depends(require_api_key)) -> GmailStatusResponse:
    """Report whether Gmail is configured and each capability's label resolves."""
    write_enabled = is_gmail_write_enabled()
    loaded = read_capability_config()
    labels = [
        CapabilityLabelResponse(capability_key=capability.key, label=label)
        for capability in loaded.enabled()
        for label in capability.gmail.labels
    ]

    credentials = get_gmail_credentials()
    if credentials is None:
        return GmailStatusResponse(
            configured=False,
            write_enabled=write_enabled,
            labels=labels,
            detail=(
                "Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN "
                "to enable Gmail intake."
            ),
        )

    try:
        async with open_gmail_client(credentials) as client:
            resolved = [
                CapabilityLabelResponse(
                    capability_key=entry.capability_key,
                    label=entry.label,
                    found=await client.resolve_label_id(entry.label) is not None,
                )
                for entry in labels
            ]
    except GmailError as exc:
        logger.error("gmail status check failed: %s", type(exc).__name__)
        return GmailStatusResponse(
            configured=True,
            write_enabled=write_enabled,
            labels=labels,
            detail=str(exc),
        )

    missing = [entry.label for entry in resolved if not entry.found]
    return GmailStatusResponse(
        configured=True,
        write_enabled=write_enabled,
        labels=resolved,
        detail=f"Gmail has no label named {', '.join(missing)}." if missing else None,
    )


@router.get("/gmail/labels", response_model=list[str])
async def read_gmail_labels(_: None = Depends(require_api_key)) -> list[str]:
    """List the mailbox's label names, so configuration can name them exactly."""
    credentials = get_gmail_credentials()
    if credentials is None:
        raise HTTPException(status_code=503, detail="Gmail credentials are not configured.")

    try:
        async with open_gmail_client(credentials) as client:
            payload = await client.request("/labels")
    except GmailAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GmailError as exc:
        logger.error("gmail label read failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    names = [
        label["name"]
        for label in payload.get("labels") or []
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    ]
    return sorted(names)


@router.post("/gmail/sync", response_model=GmailSyncResponse)
async def sync_gmail(
    _: None = Depends(require_api_key),
    limit: int = Query(default=DEFAULT_SYNC_LIMIT, ge=1, le=MAX_SYNC_LIMIT),
    prune: bool = Query(default=False),
) -> GmailSyncResponse:
    """Record inbox threads carrying each capability's labels as evidence.

    Reads only, as far as Gmail and Monday are concerned: no label changes, no
    task, no classification. `prune` deletes local evidence for threads that
    have left the inbox-scoped set.
    """
    credentials = get_gmail_credentials()
    if credentials is None:
        raise HTTPException(status_code=503, detail="Gmail credentials are not configured.")

    capabilities = read_capability_config().enabled()

    try:
        async with open_gmail_client(credentials) as client:
            with session_scope() as session:
                result = await sync_gmail_evidence(
                    client,
                    session,
                    capabilities,
                    limit=limit,
                    prune=prune,
                )
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PruneScanTruncated as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GmailAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GmailError as exc:
        logger.error("gmail sync failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return GmailSyncResponse(
        scope=result.scope.name,
        labels=result.labels,
        scanned=result.scanned,
        created=result.created,
        updated=result.updated,
        unchanged=result.unchanged,
        removed=result.removed,
        warnings=result.warnings,
    )


class ClassifyResponse(BaseModel):
    classifier_version: str
    scanned: int
    created: int
    unchanged: int


class ReviewItemResponse(BaseModel):
    classification_id: str
    evidence_id: str
    source_thread_id: str
    subject: str | None
    received_at: datetime | None
    disposition: str
    rationale: str | None


class ReviewQueueResponse(BaseModel):
    count: int
    items: list[ReviewItemResponse]


@router.post("/classify", response_model=ClassifyResponse)
def classify(
    _: None = Depends(require_api_key),
) -> ClassifyResponse:
    """Classify evidence that has no classification from the current version.

    Creates no Monday task and touches no mailbox. Version 1 routes everything
    to review, so this only populates the queue.
    """
    try:
        with session_scope() as session:
            result = classify_evidence(session)
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ClassifyResponse(
        classifier_version=result.classifier_version,
        scanned=result.scanned,
        created=result.created,
        unchanged=result.unchanged,
    )


@router.get("/review-queue", response_model=ReviewQueueResponse)
def read_review_items(
    _: None = Depends(require_api_key),
    limit: int = Query(default=DEFAULT_REVIEW_LIMIT, ge=1, le=MAX_REVIEW_LIMIT),
) -> ReviewQueueResponse:
    """List the evidence awaiting a human decision, newest first."""
    try:
        with session_scope() as session:
            items = [
                ReviewItemResponse(
                    classification_id=item.classification_id,
                    evidence_id=item.evidence_id,
                    source_thread_id=item.source_thread_id,
                    subject=item.subject,
                    received_at=item.received_at,
                    disposition=item.disposition,
                    rationale=item.rationale,
                )
                for item in read_review_queue(session, limit)
            ]
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ReviewQueueResponse(count=len(items), items=items)


class BoardItemResponse(BaseModel):
    item_id: str
    name: str
    group: str | None
    status: str | None
    admin_os_id: str | None
    action_date: str | None


class BoardItemsResponse(BaseModel):
    board_id: str
    filter: ItemFilter
    contains: str | None
    count: int
    items: list[BoardItemResponse]


class DuplicateCheckRequest(BaseModel):
    titles: list[str]
    filter: ItemFilter = ItemFilter.ALL
    match_limit: int = DEFAULT_MATCH_LIMIT
    threshold: float = CANDIDATE_SCORE


class DuplicateMatchResponse(BaseModel):
    item_id: str
    name: str
    status: str | None
    group: str | None
    admin_os_id: str | None
    score: float
    is_done: bool
    is_strong: bool


class DuplicateReportResponse(BaseModel):
    title: str
    normalized_title: str
    has_strong_match: bool
    matches: list[DuplicateMatchResponse]


class DuplicateCheckResponse(BaseModel):
    board_id: str
    filter: ItemFilter
    compared: int
    strong_match_score: float
    reports: list[DuplicateReportResponse]


@router.get("/monday/board", response_model=BoardItemsResponse)
async def read_board_items(
    _: None = Depends(require_api_key),
    item_filter: ItemFilter = Query(default=ItemFilter.OPEN, alias="filter"),
    contains: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=DEFAULT_BOARD_LIMIT, ge=1, le=MAX_BOARD_LIMIT),
) -> BoardItemsResponse:
    """List To Do List items, optionally filtered by status and name."""
    board_id, items = await list_todo_items(item_filter, contains, limit)
    return BoardItemsResponse(
        board_id=board_id,
        filter=item_filter,
        contains=contains,
        count=len(items),
        items=[
            BoardItemResponse(
                item_id=item.item_id,
                name=item.name,
                group=item.group,
                status=item.status,
                admin_os_id=item.admin_os_id,
                action_date=item.action_date,
            )
            for item in items
        ],
    )


class CreateTaskRequest(BaseModel):
    evidence_id: str
    title: str = Field(min_length=1, max_length=255)
    action_date: str | None = None
    confirmed: bool = False


class CreateTaskResponse(BaseModel):
    run_id: str
    operational_object_id: str
    admin_os_id: str
    item_id: str
    board_id: str
    title: str
    adopted: bool
    confirmed: bool
    verified: bool
    duplicates: DuplicateReportResponse


class TaskRefusedResponse(BaseModel):
    detail: str
    duplicates: DuplicateReportResponse


@router.post(
    "/monday/tasks",
    response_model=CreateTaskResponse,
    responses={409: {"model": TaskRefusedResponse}},
)
async def create_task(
    request: CreateTaskRequest,
    _: None = Depends(require_api_key),
) -> CreateTaskResponse:
    """Create one Monday task from one piece of evidence, and verify it landed.

    Refuses with `409` unless the classifier is certain and the board holds
    nothing resembling the title — `confirmed: true` is a human overriding that
    refusal, and is the only way an uncertain task gets created.
    """
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Provide a non-empty title.")

    token = get_monday_token()
    if token is None:
        raise HTTPException(status_code=503, detail="MONDAY_API_TOKEN is not configured.")

    board_id, items = await list_todo_items(ItemFilter.ALL, None, MAX_BOARD_LIMIT)

    try:
        async with open_monday_writer(token) as writer:
            with session_scope() as session:
                result = await create_task_from_evidence(
                    session,
                    writer,
                    board_id,
                    request.evidence_id,
                    title,
                    items,
                    group_id=get_todo_group_id(),
                    action_date=request.action_date,
                    confirmed=request.confirmed,
                )
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EvidenceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskCreationRefused as exc:
        raise HTTPException(
            status_code=409,
            detail=TaskRefusedResponse(
                detail=exc.reason,
                duplicates=build_report_response(exc.report),
            ).model_dump(),
        ) from exc
    except VerificationFailed as exc:
        logger.error("monday task verification failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MondayAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MondayError as exc:
        logger.error("monday task creation failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return CreateTaskResponse(
        run_id=result.run_id,
        operational_object_id=result.operational_object_id,
        admin_os_id=result.admin_os_id,
        item_id=result.item_id,
        board_id=result.board_id,
        title=result.title,
        adopted=result.adopted,
        confirmed=result.confirmed,
        verified=True,
        duplicates=build_report_response(result.report),
    )


@router.post("/monday/duplicate-check", response_model=DuplicateCheckResponse)
async def check_duplicates(
    request: DuplicateCheckRequest,
    _: None = Depends(require_api_key),
) -> DuplicateCheckResponse:
    """Rank existing board items against proposed task titles.

    Reads the board and writes nothing. Completed items are compared too: on a
    board of recurring obligations the useful answer is often "you did this
    last year".
    """
    titles = [title.strip() for title in request.titles if title.strip()]
    if not titles:
        raise HTTPException(status_code=422, detail="Provide at least one non-empty title.")
    if len(titles) > MAX_DUPLICATE_TITLES:
        raise HTTPException(
            status_code=422,
            detail=f"Provide at most {MAX_DUPLICATE_TITLES} titles per request.",
        )

    board_id, items = await list_todo_items(request.filter, None, MAX_BOARD_LIMIT)

    reports = [
        build_report_response(
            find_duplicates(
                title,
                items,
                limit=request.match_limit,
                threshold=request.threshold,
            )
        )
        for title in titles
    ]

    return DuplicateCheckResponse(
        board_id=board_id,
        filter=request.filter,
        compared=len(items),
        strong_match_score=STRONG_MATCH_SCORE,
        reports=reports,
    )


def build_report_response(report: DuplicateReport) -> DuplicateReportResponse:
    return DuplicateReportResponse(
        title=report.title,
        normalized_title=report.normalized_title,
        has_strong_match=report.has_strong_match,
        matches=[
            DuplicateMatchResponse(
                item_id=match.item_id,
                name=match.name,
                status=match.status,
                group=match.group,
                admin_os_id=match.admin_os_id,
                score=match.score,
                is_done=match.is_done,
                is_strong=match.is_strong,
            )
            for match in report.matches
        ],
    )


class ScopeFilterResponse(BaseModel):
    column_id: str
    column_title: str
    labels: list[str]
    indexes: list[int]


class BoardScopeResponse(BaseModel):
    """What the playbook's Monday scope resolves to on the live board."""

    configured: bool
    resolved: bool
    board_id: str | None = None
    board_name: str | None = None
    describes: str | None = None
    filters: list[ScopeFilterResponse] = Field(default_factory=list)
    items: int | None = None
    detail: str | None = None


@router.get("/monday/scope", response_model=BoardScopeResponse)
async def read_monday_scope(
    _: None = Depends(require_api_key),
    limit: int = Query(default=DEFAULT_ITEM_LIMIT, ge=1, le=MAX_BOARD_LIMIT),
) -> BoardScopeResponse:
    """Check the playbook's Monday scope against the board, and count what it takes.

    Read-only, and the honest answer to "is this configured correctly?". A
    scope that names a column or a label the board does not have is reported
    here with what the board has instead, rather than discovered halfway
    through a review by returning the wrong work.
    """
    token = get_monday_token()
    if token is None:
        raise HTTPException(status_code=503, detail="MONDAY_API_TOKEN is not configured.")

    loaded = read_capability_config()
    try:
        with session_scope() as session:
            config = read_active_playbook(session, loaded).document.sources.monday
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if config is None:
        return BoardScopeResponse(
            configured=False,
            resolved=False,
            detail=(
                "The playbook names no Monday board, so no Monday work is in "
                "scope. Name the board, the columns and the exact labels."
            ),
        )

    try:
        async with open_monday_client(token) as client:
            scope = await resolve_board_scope(client, config)
            items = await read_scoped_items(client, scope, limit=limit)
    except BoardScopeUnresolved as exc:
        return BoardScopeResponse(
            configured=True,
            resolved=False,
            board_id=config.board_id,
            detail=str(exc),
        )
    except MondayAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MondayError as exc:
        logger.error("monday scope read failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return BoardScopeResponse(
        configured=True,
        resolved=True,
        board_id=scope.board_id,
        board_name=scope.board_name,
        describes=scope.describes(),
        filters=[
            ScopeFilterResponse(
                column_id=filter.column_id,
                column_title=filter.column_title,
                labels=list(filter.labels),
                indexes=list(filter.indexes),
            )
            for filter in scope.filters
        ],
        items=len(items),
    )


async def list_todo_items(
    item_filter: ItemFilter,
    contains: str | None,
    limit: int,
) -> tuple[str, list[MondayItem]]:
    """Read the To Do List board, the only board in the slice's scope."""
    token = get_monday_token()
    if token is None:
        raise HTTPException(status_code=503, detail="MONDAY_API_TOKEN is not configured.")

    board_id = get_todo_board_id()
    if board_id is None:
        raise HTTPException(status_code=503, detail="TODO_BOARD_ID is not configured.")

    try:
        async with open_monday_client(token) as client:
            items = await client.list_items(
                board_id,
                item_filter=item_filter,
                contains=contains,
                limit=limit,
            )
    except MondayAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MondayError as exc:
        logger.error("monday board read failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return board_id, items


def describe_capability(capability: CapabilityConfig) -> CapabilityResponse:
    return CapabilityResponse(
        key=capability.key,
        name=capability.name,
        enabled=capability.enabled,
        position=capability.position,
        description=capability.description,
        labels=list(capability.gmail.labels),
        playbook=capability.playbook.id,
        playbook_steps=[step.value for step in capability.playbook.steps],
        policy_version=capability.recommendation_policy.version,
        categories=list(capability.recommendation_policy.categories),
        allowed_actions=[action.value for action in capability.allowed_actions],
        auto_approve=[action.value for action in capability.approval.auto_approve],
        objectives=list(capability.objectives.default_keys),
    )


def read_current_revision() -> str | None:
    """Return the applied Alembic revision, or None if the schema is absent."""
    with engine.get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
        if not inspect(connection).has_table("alembic_version"):
            return None
        row = connection.execute(text("SELECT version_num FROM alembic_version")).first()

    if row is None:
        return None
    return str(row[0])
