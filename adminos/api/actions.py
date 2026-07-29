from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from adminos.adapters.gmail import GmailError, open_gmail_client
from adminos.api.deps import read_capability, read_capability_config
from adminos.api.review import lookup_item, open_review, refuse_abandoned
from adminos.api.security import require_api_key
from adminos.capabilities.config import LoadedCapabilities
from adminos.config import GmailCredentials, get_gmail_credentials, is_gmail_write_enabled
from adminos.db.models import (
    ActionScope,
    JsonObject,
    ReviewAction,
    ReviewGroup,
    ReviewItem,
    ReviewRun,
)
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
    read_item_actions,
    verify_action,
)
from adminos.domain.review import (
    HUMAN_ACTOR,
    ItemState,
    ReviewNotFound,
    read_group,
    read_group_items,
)
from adminos.domain.scopes import (
    ExcludedItem,
    ScopeMismatch,
    ScopeNotFound,
    check_scope_current,
    check_scope_invariant,
    check_scope_matches,
    open_scope,
    read_scope,
    settle_scope,
)
from adminos.logging import get_logger


logger = get_logger(__name__)
router = APIRouter(prefix="/review", tags=["actions"])

WITHDRAWN_ITEM_STATES = {ItemState.PENDING, ItemState.DISMISSED, ItemState.DEFERRED}
"""States that mean the approval a scope was built on no longer stands."""


class PrepareRequest(BaseModel):
    """Exactly which rows to prepare, and nothing wider.

    `item_ids` is the selection. `entire_capability` is the only way to mean
    "all of them", and it has to be said: a request that names neither is
    refused rather than read as everything.
    """

    capability_key: str | None = None
    item_ids: list[str] | None = None
    entire_capability: bool = Field(
        default=False,
        description=(
            "Only true when the user asked for every approved row in the "
            "capability. Requires capability_key."
        ),
    )


class ExecuteRequest(BaseModel):
    """The prepared scope to run, named by id and restated in full.

    `item_ids` and `action_ids` are what the caller believes it is confirming,
    and they are required rather than offered: a caller that cannot restate
    the scope has not read it, and this is the one request that writes to
    Gmail. If either disagrees with the preparation, nothing runs.
    """

    scope_id: str
    item_ids: list[str]
    action_ids: list[str]
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


class PreparedItemResponse(BaseModel):
    item_id: str
    action_id: str
    action: str
    thread_id: str | None


class ExcludedItemResponse(BaseModel):
    item_id: str
    reason: str


class PrepareResponse(ActionsResponse):
    """What was prepared, what was left out, and the id that runs it.

    Every part of the scope is stated rather than implied, so a reader never
    has to work out which rows a confirmation would cover.
    """

    scope_id: str
    state: str = Field(
        description=(
            "current while the scope may still be executed; superseded once a "
            "later preparation replaced it, executed once it has run."
        )
    )
    capability_key: str | None
    entire_capability: bool
    requested_item_ids: list[str]
    prepared_item_ids: list[str]
    action_ids: list[str]
    prepared_items: list[PreparedItemResponse]
    excluded_items: list[ExcludedItemResponse]
    scope_matches_request: bool = Field(
        description=(
            "True when every requested item was prepared and nothing else was. "
            "False means the difference must be shown before anything is confirmed."
        )
    )


@router.post("/runs/{run_id}/actions/prepare", response_model=PrepareResponse)
def prepare_actions(
    run_id: str,
    request: PrepareRequest,
    _: None = Depends(require_api_key),
) -> PrepareResponse:
    """Turn the selected approvals into exact plans. Nothing is written to Gmail.

    Preparing is what makes an approval inspectable, and what fixes its scope:
    the rows named here are the only rows the resulting `scope_id` can ever
    execute. Preparing again supersedes the previous scope.
    """
    loaded = read_capability_config()

    with open_review(run_id) as (session, run):
        refuse_abandoned(run)
        requested = requested_item_ids(session, loaded, run, request)
        prepared, excluded = prepare_selected(session, loaded, run, request, requested)
        scope = open_scope(
            session,
            run,
            capability_key=request.capability_key,
            entire_capability=request.entire_capability,
            requested_item_ids=requested,
            actions=prepared,
            excluded=excluded,
            actor=HUMAN_ACTOR,
        )
        return build_prepare_response(session, scope, prepared)


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


@router.get("/runs/{run_id}/scopes/{scope_id}", response_model=PrepareResponse)
def get_scope(
    run_id: str,
    scope_id: str,
    _: None = Depends(require_api_key),
) -> PrepareResponse:
    """Read a prepared scope back: what it covers, and whether it still stands."""
    with open_review(run_id) as (session, run):
        scope = lookup_scope(session, run, scope_id)
        actions = [lookup_action(session, run, action_id) for action_id in scope.action_ids]
        return build_prepare_response(session, scope, actions)


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
    response = await run_actions(run_id, request=None, retry_action_id=action_id)
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
        refuse_abandoned(run)
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
    request: ExecuteRequest | None,
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
            refuse_abandoned(run)
            scope, actions = select_actions(session, run, request, retry_action_id)
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
            if scope is not None:
                settle_scope(
                    session,
                    scope,
                    executed=executed,
                    verified=[
                        action
                        for action in executed
                        if action.state == ActionState.COMPLETED
                    ],
                )
            return build_actions_response(session, executed)


def select_actions(
    session: Session,
    run: ReviewRun,
    request: ExecuteRequest | None,
    retry_action_id: str | None,
) -> tuple[ActionScope | None, list[ReviewAction]]:
    """The actions a scope authorises, refusing everything it does not.

    Every check happens here, before the first write: a scope that is stale,
    superseded, or not the selection being confirmed answers 409 and leaves
    the mailbox alone. Retrying one failed action is the only path without a
    scope, and it names a single action explicitly.
    """
    if retry_action_id is not None or request is None:
        if retry_action_id is None:
            raise HTTPException(status_code=400, detail="There is nothing to execute.")
        return None, [lookup_action(session, run, retry_action_id)]

    scope = lookup_scope(session, run, request.scope_id)
    try:
        check_scope_current(scope)
        check_scope_matches(scope, request.item_ids)
        check_action_ids(scope, request.action_ids)
        actions = [lookup_action(session, run, action_id) for action_id in scope.action_ids]
        check_scope_invariant(scope, actions)
        check_decisions_stand(scope, session, actions)
    except ScopeMismatch as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    return scope, actions


def check_action_ids(scope: ActionScope, action_ids: Sequence[str]) -> None:
    """Refuse action ids that are not the ones this scope prepared."""
    prepared = set(scope.action_ids)
    asked = set(action_ids)
    if prepared == asked:
        return
    raise ScopeMismatch(
        "The action ids being confirmed are not the ones that were prepared, "
        "so nothing was executed.",
        {
            "scope_id": scope.id,
            "prepared_action_ids": sorted(prepared),
            "requested_action_ids": sorted(asked),
            "not_prepared": sorted(asked - prepared),
            "prepared_but_not_requested": sorted(prepared - asked),
        },
    )


def check_decisions_stand(
    scope: ActionScope,
    session: Session,
    actions: list[ReviewAction],
) -> None:
    """Every item in the scope must still be the approval that was prepared.

    The first leg of the invariant: an item re-decided between preparation and
    confirmation is no longer the thing that was agreed to, so the whole scope
    stops rather than running the part that still matches.
    """
    withdrawn: list[str] = []
    for action in actions:
        item = session.get(ReviewItem, action.item_id)
        if item is None or item.state in WITHDRAWN_ITEM_STATES:
            withdrawn.append(action.item_id)
            continue
        if decided_after(item, scope):
            withdrawn.append(action.item_id)
    if withdrawn:
        raise ScopeMismatch(
            "Some items were decided again after they were prepared, so nothing "
            "was executed.",
            {
                "scope_id": scope.id,
                "prepared_item_ids": sorted(scope.prepared_item_ids),
                "no_longer_approved": sorted(withdrawn),
            },
        )


def decided_after(item: ReviewItem, scope: ActionScope) -> bool:
    """Was this item decided again after the scope was prepared?

    A decision recorded after preparation is a different instruction from the
    one being confirmed, whatever it says.
    """
    if item.decided_at is None:
        return True
    return as_utc(item.decided_at) > as_utc(scope.created_at)


def as_utc(moment: datetime) -> datetime:
    """Read a stored timestamp as UTC; SQLite hands them back without a zone."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def requested_item_ids(
    session: Session,
    loaded: LoadedCapabilities,
    run: ReviewRun,
    request: PrepareRequest,
) -> list[str]:
    """The exact rows the caller selected, refusing to guess at "all of them".

    A request that names no items and does not say `entire_capability` is a
    request whose scope is unknown. It is refused: reading it as the whole
    capability is how nineteen rows became twenty-two.
    """
    if request.item_ids is not None:
        selected = list(dict.fromkeys(request.item_ids))
        if not selected:
            raise HTTPException(
                status_code=400,
                detail="item_ids was empty, so there is nothing to prepare.",
            )
        if request.entire_capability:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Send either item_ids or entire_capability=true, not both: "
                    "they describe different selections."
                ),
            )
        return selected

    if not request.entire_capability:
        raise HTTPException(
            status_code=400,
            detail=(
                "Preparation needs the exact item_ids that were selected. Send "
                "entire_capability=true with a capability_key only when every "
                "approved row in that capability was asked for."
            ),
        )
    if request.capability_key is None:
        raise HTTPException(
            status_code=400,
            detail="entire_capability=true needs the capability_key it applies to.",
        )

    capability = read_capability(loaded, request.capability_key)
    try:
        group = read_group(session, run, capability.key)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        item.id
        for item in read_group_items(session, group)
        if item.state == ItemState.APPROVED
    ]


def prepare_selected(
    session: Session,
    loaded: LoadedCapabilities,
    run: ReviewRun,
    request: PrepareRequest,
    item_ids: list[str],
) -> tuple[list[ReviewAction], list[ExcludedItem]]:
    """Prepare one action per selected row, naming every row left out.

    A row that cannot be prepared is reported rather than dropped: silence
    about an exclusion is what makes a caller infer a scope.
    """
    prepared: list[ReviewAction] = []
    excluded: list[ExcludedItem] = []

    for item_id in item_ids:
        item = session.get(ReviewItem, item_id)
        if item is None or item.run_id != run.id:
            excluded.append(ExcludedItem(item_id, "This run has no such item."))
            continue

        capability_key = read_item_capability(session, item)
        if request.capability_key is not None and capability_key != request.capability_key:
            excluded.append(
                ExcludedItem(
                    item_id,
                    f"The item belongs to {capability_key!r}, not "
                    f"{request.capability_key!r}.",
                )
            )
            continue

        capability = read_capability(loaded, capability_key)
        ensure_actions(session, capability, run, [item])
        recorded = read_item_actions(session, run, item)
        outstanding = [
            action for action in recorded if action.state != ActionState.COMPLETED
        ]
        if not outstanding:
            excluded.append(ExcludedItem(item_id, why_nothing_to_prepare(item, recorded)))
            continue

        for action in outstanding:
            try:
                prepared.append(prepare_action(session, capability, action))
            except ActionRefused as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

    return prepared, excluded


def why_nothing_to_prepare(item: ReviewItem, recorded: list[ReviewAction]) -> str:
    if recorded:
        return "Everything approved for this item has already been completed."
    return f"The item is {item.state}, so there is no approved action to prepare."


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


def lookup_scope(session: Session, run: ReviewRun, scope_id: str) -> ActionScope:
    try:
        return read_scope(session, run, scope_id)
    except ScopeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def build_prepare_response(
    session: Session,
    scope: ActionScope,
    prepared: list[ReviewAction],
) -> PrepareResponse:
    base = build_actions_response(session, prepared)
    excluded = [
        ExcludedItemResponse(item_id=entry["item_id"], reason=entry["reason"])
        for entry in scope.excluded
    ]
    return PrepareResponse(
        gmail_write_enabled=base.gmail_write_enabled,
        counts=base.counts,
        actions=base.actions,
        scope_id=scope.id,
        state=scope.state,
        capability_key=scope.capability_key,
        entire_capability=scope.entire_capability,
        requested_item_ids=list(scope.requested_item_ids),
        prepared_item_ids=list(scope.prepared_item_ids),
        action_ids=list(scope.action_ids),
        prepared_items=[
            PreparedItemResponse(
                item_id=action.item_id,
                action_id=action.id,
                action=action.action_kind,
                thread_id=action.target_thread_id,
            )
            for action in prepared
        ],
        excluded_items=excluded,
        scope_matches_request=(
            not excluded
            and set(scope.prepared_item_ids) == set(scope.requested_item_ids)
        ),
    )


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
