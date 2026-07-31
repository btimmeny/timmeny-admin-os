"""The playbook as an object Brian can read and change, one revision at a time.

Every change takes the same route: proposed, read back as the exact effect,
and confirmed. Nothing here activates anything without a second request, which
is what keeps "let's do objectives first today" from quietly becoming how every
morning works.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from adminos.adapters.monday import MondayAuthError, MondayError, open_monday_client
from adminos.api.deps import read_capability_config
from adminos.api.security import require_api_key
from adminos.capabilities.config import LoadedCapabilities
from adminos.config import get_monday_token
from adminos.db.engine import DatabaseNotConfigured, session_scope
from adminos.db.models import PlaybookRevision
from adminos.domain.activities import ACTIVITIES, Availability
from adminos.domain.boards import BoardScopeUnresolved, resolve_board_scope
from adminos.domain.playbook import (
    DEFAULT_PLAYBOOK_ID,
    ChangeRefused,
    MondayScopeConfig,
    PlaybookChange,
    PlaybookDocument,
    PlaybookError,
    SetMondayScope,
    ValidationReport,
    read_playbook,
    validate_playbook,
)
from adminos.domain.playbook_store import (
    ActivePlaybook,
    RevisionNotFound,
    RevisionRefused,
    confirm_revision,
    propose_change,
    read_active_playbook,
    read_revision,
    read_revisions,
)
from adminos.logging import get_logger


logger = get_logger(__name__)
router = APIRouter(prefix="/playbook", tags=["playbook"])


class ValidationMessageResponse(BaseModel):
    path: str
    code: str
    message: str


class ValidationResponse(BaseModel):
    """Whether this playbook can be run, and what is wrong where it cannot.

    Errors and warnings say different things. An error means the revision is
    never activated. A warning means it runs while being honest about itself —
    most often that it contains an activity Admin OS cannot perform yet.
    """

    valid: bool
    errors: list[ValidationMessageResponse]
    warnings: list[ValidationMessageResponse]


class StepResponse(BaseModel):
    capability_key: str
    label: str
    enabled: bool
    order: int


class ActivityResponse(BaseModel):
    activity_key: str
    label: str
    enabled: bool
    order: int
    fresh_data_required: bool
    intro: str
    availability: str
    """`built` if a session can work it, `planned` if it is named and not
    implemented here yet."""
    steps: list[StepResponse]


class ScopeFilterResponse(BaseModel):
    column_id: str
    labels: list[str]


class MondayScopeResponse(BaseModel):
    """Which Monday items count as today's work, as the playbook names them.

    The ids and labels as configured, not as found on the board. Whether the
    board actually carries them is `GET /admin/monday/scope`, which reads it.
    """

    board_id: str
    match: str
    filters: list[ScopeFilterResponse]


class PlaybookDocumentResponse(BaseModel):
    schema_version: int
    playbook_id: str
    name: str
    auto_start_first_activity: bool
    finish_with_summary: bool
    activities: list[ActivityResponse]
    monday_scope: MondayScopeResponse | None = None
    """Absent means no Monday work is in scope, which is not the same as none."""


class RevisionResponse(BaseModel):
    revision_id: str
    playbook_id: str
    number: int
    status: str
    created_by: str
    created_at: datetime
    activated_at: datetime | None
    superseded_at: datetime | None
    based_on_revision_id: str | None
    change_summary: list[str]
    rationale: str | None


class PlaybookResponse(BaseModel):
    """The playbook in force, whole, with everything true about it right now."""

    revision: RevisionResponse
    playbook: PlaybookDocumentResponse
    validation: ValidationResponse
    message: str
    fell_back_from: RevisionResponse | None = None
    """The revision that was active and stopped validating, where one did.

    Set when a capability was removed from under an active playbook: the
    session runs the last revision that still works, and says which.
    """


class RevisionsResponse(BaseModel):
    playbook_id: str
    revisions: list[RevisionResponse]


class RevisionDetailResponse(BaseModel):
    revision: RevisionResponse
    playbook: PlaybookDocumentResponse
    validation: ValidationResponse


class ProposeChangeRequest(BaseModel):
    """A change to how every session works, written down for Brian to confirm.

    Not a session's ordering. "Skip objectives today" belongs on the session;
    this is for "from now on".
    """

    changes: list[PlaybookChange] = Field(min_length=1)
    rationale: str | None = None
    playbook_id: str = DEFAULT_PLAYBOOK_ID


class ConfirmRequest(BaseModel):
    confirm: bool = Field(
        description=(
            "Must be true. A persistent change to the playbook is made on Brian's "
            "word and nothing else."
        )
    )


class ProposalResponse(BaseModel):
    """What the playbook would become, and what it would take to make it so."""

    revision: RevisionResponse
    playbook: PlaybookDocumentResponse
    validation: ValidationResponse
    effect: list[str]
    """The change in sentences: what to read back before confirming."""

    order_now: list[str]
    order_after: list[str]
    message: str
    confirm_action: dict[str, str]
    """The exact request that makes this the playbook."""


@router.get("", response_model=PlaybookResponse)
def get_active_playbook(
    playbook_id: str = DEFAULT_PLAYBOOK_ID,
    _: None = Depends(require_api_key),
) -> PlaybookResponse:
    """The playbook a session would run right now."""
    loaded = read_capability_config()
    with open_database() as session:
        active = load_active(session, loaded, playbook_id)
        return build_playbook_response(active)


@router.get("/revisions", response_model=RevisionsResponse)
def list_playbook_revisions(
    playbook_id: str = DEFAULT_PLAYBOOK_ID,
    _: None = Depends(require_api_key),
) -> RevisionsResponse:
    """Every version this playbook has had, newest first."""
    with open_database() as session:
        return RevisionsResponse(
            playbook_id=playbook_id,
            revisions=[
                build_revision_response(revision)
                for revision in read_revisions(session, playbook_id)
            ],
        )


@router.get("/revisions/{revision_id}", response_model=RevisionDetailResponse)
def get_playbook_revision(
    revision_id: str,
    _: None = Depends(require_api_key),
) -> RevisionDetailResponse:
    """One version of the playbook, exactly as it stood."""
    loaded = read_capability_config()
    with open_database() as session:
        revision = find_revision(session, revision_id)
        document = parse_revision(revision)
        return RevisionDetailResponse(
            revision=build_revision_response(revision),
            playbook=build_document_response(document),
            validation=build_validation_response(validate_playbook(document, loaded)),
        )


@router.post("/propose", response_model=ProposalResponse)
async def propose_playbook_change(
    request: ProposeChangeRequest,
    _: None = Depends(require_api_key),
) -> ProposalResponse:
    """Write down what the playbook would become. Change nothing yet.

    The response is written to be read out: the effect in sentences, the order
    before and the order after. Confirming is a separate request, because a
    persistent change to how every morning works should take two.
    """
    await check_monday_scopes(request.changes)
    loaded = read_capability_config()
    with open_database() as session:
        active = load_active(session, loaded, request.playbook_id)
        try:
            proposal = propose_change(
                session,
                loaded,
                request.changes,
                rationale=request.rationale,
                playbook_id=request.playbook_id,
            )
        except ChangeRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return ProposalResponse(
            revision=build_revision_response(proposal.revision),
            playbook=build_document_response(proposal.document),
            validation=build_validation_response(proposal.report),
            effect=list(proposal.summary),
            order_now=[activity.label for activity in active.document.enabled()],
            order_after=[activity.label for activity in proposal.document.enabled()],
            message=proposal_message(proposal.summary, proposal.report),
            confirm_action={
                "operation": "confirmPlaybookChange",
                "method": "POST",
                "path": f"/playbook/revisions/{proposal.revision.id}/confirm",
            },
        )


@router.post("/revisions/{revision_id}/confirm", response_model=PlaybookResponse)
def confirm_playbook_change(
    revision_id: str,
    request: ConfirmRequest,
    _: None = Depends(require_api_key),
) -> PlaybookResponse:
    """Make a proposed revision the playbook, from the next session on.

    A session already under way keeps the revision it opened with. Rearranging
    a morning around a change made halfway through it would be answering a
    question with a different question.
    """
    return activate(revision_id, request)


@router.post("/revisions/{revision_id}/activate", response_model=PlaybookResponse)
def activate_playbook_revision(
    revision_id: str,
    request: ConfirmRequest,
    _: None = Depends(require_api_key),
) -> PlaybookResponse:
    """Put an earlier revision back in force.

    The same operation as confirming, under the name for undoing: "go back to
    how it was last week" names a revision that already exists rather than
    proposing the reverse of every change since.
    """
    return activate(revision_id, request)


def activate(revision_id: str, request: ConfirmRequest) -> PlaybookResponse:
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="A playbook revision becomes active on an explicit confirmation.",
        )
    loaded = read_capability_config()
    with open_database() as session:
        find_revision(session, revision_id)
        try:
            active = confirm_revision(session, loaded, revision_id)
        except RevisionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return build_playbook_response(active)


async def check_monday_scopes(changes: list[PlaybookChange]) -> None:
    """Find a proposed Monday scope on the live board, or refuse to propose it.

    Proposing is the moment a mistyped column or a label nobody created can
    still be corrected, and the board is the only thing that knows. Monday
    does not complain about a filter that names nothing: it matches nothing,
    and a filter matching nothing on a thousand-item board hands back the whole
    board looking exactly like today's work. So the check happens here rather
    than at review time, and an unreachable Monday is a refusal too — a scope
    nobody could check is not a scope Brian can confirm.
    """
    wanted = [change for change in changes if isinstance(change, SetMondayScope)]
    if not wanted:
        return

    token = get_monday_token()
    if token is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "MONDAY_API_TOKEN is not configured, so this scope cannot be "
                "checked against the board. A scope nobody checked is a filter "
                "that may match nothing and return the whole board."
            ),
        )

    async with open_monday_client(token) as client:
        for change in wanted:
            config = MondayScopeConfig(board_id=change.board_id, filters=change.filters)
            try:
                await resolve_board_scope(client, config)
            except BoardScopeUnresolved as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except MondayAuthError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            except MondayError as exc:
                logger.error("monday scope check failed: %s", type(exc).__name__)
                raise HTTPException(status_code=502, detail=str(exc)) from exc


@contextmanager
def open_database() -> Iterator[Session]:
    """The session scope, with an unconfigured database reported as 503."""
    try:
        with session_scope() as session:
            yield session
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def load_active(
    session: Session, loaded: LoadedCapabilities, playbook_id: str
) -> ActivePlaybook:
    try:
        return read_active_playbook(session, loaded, playbook_id)
    except (PlaybookError, RevisionRefused) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def find_revision(session: Session, revision_id: str) -> PlaybookRevision:
    try:
        return read_revision(session, revision_id)
    except RevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def parse_revision(revision: PlaybookRevision) -> PlaybookDocument:
    try:
        return read_playbook(dict(revision.document))
    except PlaybookError as exc:  # pragma: no cover - stored documents are written valid
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def build_playbook_response(active: ActivePlaybook) -> PlaybookResponse:
    return PlaybookResponse(
        revision=build_revision_response(active.revision),
        playbook=build_document_response(active.document),
        validation=build_validation_response(active.report),
        message=playbook_message(active),
        fell_back_from=(
            build_revision_response(active.fell_back_from)
            if active.fell_back_from is not None
            else None
        ),
    )


def build_revision_response(revision: PlaybookRevision) -> RevisionResponse:
    return RevisionResponse(
        revision_id=revision.id,
        playbook_id=revision.playbook_id,
        number=revision.number,
        status=revision.status,
        created_by=revision.created_by,
        created_at=revision.created_at,
        activated_at=revision.activated_at,
        superseded_at=revision.superseded_at,
        based_on_revision_id=revision.based_on_revision_id,
        change_summary=list(revision.change_summary or []),
        rationale=revision.rationale,
    )


def build_document_response(document: PlaybookDocument) -> PlaybookDocumentResponse:
    return PlaybookDocumentResponse(
        schema_version=document.schema_version,
        playbook_id=document.playbook_id,
        name=document.name,
        auto_start_first_activity=document.session.auto_start_first_activity,
        finish_with_summary=document.session.finish_with_summary,
        activities=[
            ActivityResponse(
                activity_key=activity.activity_key,
                label=activity.label,
                enabled=activity.enabled,
                order=activity.order,
                fresh_data_required=activity.fresh_data_required,
                intro=activity.intro,
                availability=availability_of(activity.activity_key),
                steps=[
                    StepResponse(
                        capability_key=step.capability_key,
                        label=step.label,
                        enabled=step.enabled,
                        order=step.order,
                    )
                    for step in sorted(activity.steps, key=lambda step: step.order)
                ],
            )
            for activity in document.ordered()
        ],
        monday_scope=build_monday_scope_response(document.sources.monday),
    )


def build_monday_scope_response(
    scope: MondayScopeConfig | None,
) -> MondayScopeResponse | None:
    if scope is None:
        return None
    return MondayScopeResponse(
        board_id=scope.board_id,
        match=scope.match,
        filters=[
            ScopeFilterResponse(column_id=filter.column_id, labels=list(filter.labels))
            for filter in scope.filters
        ],
    )


def availability_of(activity_key: str) -> str:
    for activity in ACTIVITIES:
        if activity.key == activity_key:
            return activity.availability
    return Availability.PLANNED


def build_validation_response(report: ValidationReport) -> ValidationResponse:
    return ValidationResponse(
        valid=report.valid,
        errors=[
            ValidationMessageResponse(path=error.path, code=error.code, message=error.message)
            for error in report.errors
        ],
        warnings=[
            ValidationMessageResponse(
                path=warning.path, code=warning.code, message=warning.message
            )
            for warning in report.warnings
        ],
    )


def playbook_message(active: ActivePlaybook) -> str:
    """One sentence about what is in force, safe to read out as written."""
    order = ", ".join(activity.label for activity in active.document.enabled())
    said = f"Revision {active.revision.number} is in force: {order}."
    if active.fell_back_from is not None:
        said += (
            f" Revision {active.fell_back_from.number} could no longer be run and was "
            "marked invalid, so this is the last one that works."
        )
    if active.report.warnings:
        said += " " + " ".join(warning.message for warning in active.report.warnings)
    return said


def proposal_message(summary: tuple[str, ...], report: ValidationReport) -> str:
    said = "This would change the playbook for every session from now on: " + " ".join(summary)
    if not report.valid:
        return (
            said
            + " It cannot be confirmed as it stands: "
            + " ".join(error.message for error in report.errors)
        )
    return said + " Confirm it and it takes effect from the next session."
