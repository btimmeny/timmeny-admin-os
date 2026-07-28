from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from adminos.adapters.gmail import GmailError, open_gmail_client
from adminos.api.deps import read_capability, read_capability_config
from adminos.api.review import lookup_item, open_review
from adminos.api.security import require_api_key
from adminos.capabilities.config import LoadedCapabilities
from adminos.config import GmailCredentials, get_gmail_credentials, is_gmail_write_enabled
from adminos.db.models import JsonObject, ReviewAction, ReviewGroup, ReviewItem, ReviewRun
from adminos.domain.actions import (
    ActionNotFound,
    ActionRefused,
    ActionState,
    authorise_send,
    ensure_actions,
    execute_action,
    prepare_action,
    read_action,
    read_action_events,
    read_actions,
    verify_action,
)
from adminos.domain.review import HUMAN_ACTOR, ReviewNotFound, read_group, read_group_items
from adminos.logging import get_logger


logger = get_logger(__name__)
router = APIRouter(prefix="/review", tags=["actions"])


class PrepareRequest(BaseModel):
    capability_key: str | None = None
    item_ids: list[str] | None = None
    action_ids: list[str] | None = None


class ExecuteRequest(BaseModel):
    capability_key: str | None = None
    action_ids: list[str] | None = None
    confirm: bool = Field(
        default=False,
        description="Executing writes to Gmail, so it must be asked for explicitly.",
    )


class SendDraftRequest(BaseModel):
    """Approval of one exact draft.

    Both ids are required: the draft, and the message it contained when it was
    read back. A draft edited since then will not send.
    """

    draft_id: str
    draft_message_id: str
    confirm: bool = False


class ActionEventResponse(BaseModel):
    sequence: int
    event: str
    state_after: str
    detail: JsonObject | None
    external_ref: str | None
    at: datetime


class ActionResponse(BaseModel):
    action_id: str
    item_id: str
    capability_key: str
    action: str
    state: str
    params: JsonObject | None
    prepared_params: JsonObject | None
    idempotency_key: str
    thread_id: str | None
    external_kind: str | None
    external_ref: str | None
    approval_kind: str
    approved_by: str
    rule_id: str | None
    attempts: int
    last_error: str | None
    verification: JsonObject | None
    prepared_at: datetime | None
    executed_at: datetime | None
    verified_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    events: list[ActionEventResponse] = Field(default_factory=list)


class ActionsResponse(BaseModel):
    gmail_write_enabled: bool
    counts: dict[str, int]
    actions: list[ActionResponse]


@router.post("/runs/{run_id}/actions/prepare", response_model=ActionsResponse)
def prepare_actions(
    run_id: str,
    request: PrepareRequest,
    _: None = Depends(require_api_key),
) -> ActionsResponse:
    """Turn approvals into exact plans. Nothing is written to Gmail.

    Preparing is what makes an approval inspectable: the resolved parameters
    and the idempotency key can be read before anything happens.
    """
    loaded = read_capability_config()

    with open_review(run_id) as (session, run):
        actions = collect_actions(session, loaded, run, request.capability_key, request.item_ids)
        if request.action_ids is not None:
            wanted = set(request.action_ids)
            actions = [action for action in actions if action.id in wanted]

        prepared: list[ReviewAction] = []
        for action in actions:
            capability = read_capability(loaded, action.capability_key)
            try:
                prepared.append(prepare_action(session, capability, action))
            except ActionRefused as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return build_actions_response(session, prepared)


@router.get("/runs/{run_id}/actions", response_model=ActionsResponse)
def list_actions(
    run_id: str,
    capability_key: str | None = None,
    state: ActionState | None = None,
    _: None = Depends(require_api_key),
) -> ActionsResponse:
    """Every action on a run, with its state, verification, and failures."""
    with open_review(run_id) as (session, run):
        actions = read_actions(
            session,
            run,
            capability_key=capability_key,
            states=[state] if state is not None else None,
        )
        return build_actions_response(session, actions)


@router.get("/runs/{run_id}/actions/{action_id}", response_model=ActionResponse)
def get_action(
    run_id: str,
    action_id: str,
    _: None = Depends(require_api_key),
) -> ActionResponse:
    """One action and its whole audit history."""
    with open_review(run_id) as (session, run):
        return build_action_response(session, lookup_action(session, run, action_id))


@router.post("/runs/{run_id}/actions/execute", response_model=ActionsResponse)
async def execute_actions(
    run_id: str,
    request: ExecuteRequest,
    _: None = Depends(require_api_key),
) -> ActionsResponse:
    """Execute prepared actions and verify each one by reading it back.

    Four separate gates stand between an approval and a change in the mailbox:
    the capability must be allowed the action, permitted to execute it,
    `GMAIL_WRITE_ENABLED` must be on, and the caller must confirm.
    """
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Executing changes the mailbox. Send confirm=true to proceed.",
        )
    return await run_actions(run_id, request, retry_action_id=None)


@router.post("/runs/{run_id}/actions/{action_id}/retry", response_model=ActionResponse)
async def retry_action(
    run_id: str,
    action_id: str,
    _: None = Depends(require_api_key),
) -> ActionResponse:
    """Retry one failed action.

    The effect is verified before it is attempted again, so a step that in
    fact succeeded is adopted rather than repeated.
    """
    response = await run_actions(
        run_id,
        ExecuteRequest(action_ids=[action_id], confirm=True),
        retry_action_id=action_id,
    )
    if not response.actions:
        raise HTTPException(status_code=404, detail=f"No action {action_id!r} to retry.")
    return response.actions[0]


@router.post("/runs/{run_id}/actions/{action_id}/verify", response_model=ActionResponse)
async def verify_one_action(
    run_id: str,
    action_id: str,
    _: None = Depends(require_api_key),
) -> ActionResponse:
    """Read an executed action back from Gmail again. Writes nothing."""
    loaded = read_capability_config()
    credentials = require_gmail_credentials()

    async with open_gmail_client(credentials) as client:
        with open_review(run_id) as (session, run):
            action = lookup_action(session, run, action_id)
            capability = read_capability(loaded, action.capability_key)
            try:
                verified = await verify_action(session, client, capability, action)
            except ActionRefused as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except GmailError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            return build_action_response(session, verified)


@router.post("/runs/{run_id}/items/{item_id}/send-draft", response_model=ActionResponse)
def approve_send_draft(
    run_id: str,
    item_id: str,
    request: SendDraftRequest,
    _: None = Depends(require_api_key),
) -> ActionResponse:
    """Approve sending one exact draft. Approving does not send it.

    Creating a draft and sending it are separate acts, and the send is bound
    to the draft that was read: the resulting action still has to be prepared
    and executed, and it refuses if the draft has changed in the meantime.
    """
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Sending mail needs confirm=true along with the exact draft ids.",
        )

    loaded = read_capability_config()
    with open_review(run_id) as (session, run):
        item = lookup_item(session, run, item_id)
        capability = read_capability(loaded, read_item_capability(session, item))
        try:
            action = authorise_send(
                session,
                capability,
                run,
                item,
                draft_id=request.draft_id,
                draft_message_id=request.draft_message_id,
                actor=HUMAN_ACTOR,
            )
        except ActionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return build_action_response(session, action)


async def run_actions(
    run_id: str,
    request: ExecuteRequest,
    retry_action_id: str | None,
) -> ActionsResponse:
    loaded = read_capability_config()
    credentials = require_gmail_credentials()
    if not is_gmail_write_enabled():
        raise HTTPException(
            status_code=409,
            detail="Gmail writes are disabled. Set GMAIL_WRITE_ENABLED=true to allow them.",
        )

    async with open_gmail_client(credentials) as client:
        with open_review(run_id) as (session, run):
            actions = select_actions(session, run, request, retry_action_id)
            executed: list[ReviewAction] = []
            for action in actions:
                capability = read_capability(loaded, action.capability_key)
                try:
                    prepare_action(session, capability, action)
                    executed.append(
                        await execute_action(session, client, capability, action)
                    )
                except ActionRefused as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                except GmailError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
            return build_actions_response(session, executed)


def select_actions(
    session: Session,
    run: ReviewRun,
    request: ExecuteRequest,
    retry_action_id: str | None,
) -> list[ReviewAction]:
    if retry_action_id is not None:
        return [lookup_action(session, run, retry_action_id)]
    if request.action_ids is not None:
        return [lookup_action(session, run, action_id) for action_id in request.action_ids]
    return [
        action
        for action in read_actions(session, run, capability_key=request.capability_key)
        if action.state != ActionState.COMPLETED
    ]


def collect_actions(
    session: Session,
    loaded: LoadedCapabilities,
    run: ReviewRun,
    capability_key: str | None,
    item_ids: list[str] | None,
) -> list[ReviewAction]:
    """Record actions for newly approved items, then return the run's actions."""
    for capability in loaded.enabled():
        if capability_key is not None and capability.key != capability_key:
            continue
        try:
            group = read_group(session, run, capability.key)
        except ReviewNotFound:
            continue
        items = [
            item
            for item in read_group_items(session, group)
            if item_ids is None or item.id in set(item_ids)
        ]
        ensure_actions(session, capability, run, items)

    return read_actions(session, run, capability_key=capability_key)


def require_gmail_credentials() -> GmailCredentials:
    credentials = get_gmail_credentials()
    if credentials is None:
        raise HTTPException(
            status_code=503,
            detail="Gmail is not configured, so no action can be executed or verified.",
        )
    return credentials


def read_item_capability(session: Session, item: ReviewItem) -> str:
    group = session.get(ReviewGroup, item.group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=f"Item {item.id!r} has no group.")
    return group.capability_key


def lookup_action(session: Session, run: ReviewRun, action_id: str) -> ReviewAction:
    try:
        return read_action(session, run, action_id)
    except ActionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def build_actions_response(session: Session, actions: list[ReviewAction]) -> ActionsResponse:
    counts: dict[str, int] = {"total": len(actions)}
    for action in actions:
        counts[action.state] = counts.get(action.state, 0) + 1
    return ActionsResponse(
        gmail_write_enabled=is_gmail_write_enabled(),
        counts=counts,
        actions=[build_action_response(session, action) for action in actions],
    )


def build_action_response(session: Session, action: ReviewAction) -> ActionResponse:
    return ActionResponse(
        action_id=action.id,
        item_id=action.item_id,
        capability_key=action.capability_key,
        action=action.action_kind,
        state=action.state,
        params=action.params,
        prepared_params=action.prepared_params,
        idempotency_key=action.idempotency_key,
        thread_id=action.target_thread_id,
        external_kind=action.external_kind,
        external_ref=action.external_ref,
        approval_kind=action.approval_kind,
        approved_by=action.approved_by,
        rule_id=action.rule_id,
        attempts=action.attempts,
        last_error=action.last_error,
        verification=action.verification,
        prepared_at=action.prepared_at,
        executed_at=action.executed_at,
        verified_at=action.verified_at,
        completed_at=action.completed_at,
        failed_at=action.failed_at,
        events=[
            ActionEventResponse(
                sequence=event.sequence,
                event=event.event,
                state_after=event.state_after,
                detail=event.detail,
                external_ref=event.external_ref,
                at=event.created_at,
            )
            for event in read_action_events(session, action)
        ],
    )
