import hashlib
import json

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Awaitable, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.adapters.gmail import (
    INBOX_LABEL_ID,
    TRASH_LABEL_ID,
    GmailClient,
    GmailError,
)
from adminos.capabilities.config import (
    GMAIL_ACTIONS,
    ActionKind,
    CapabilityConfig,
    PlaybookStep,
)
from adminos.config import is_gmail_write_enabled
from adminos.db.models import (
    ActionEvent,
    JsonObject,
    ReviewAction,
    ReviewDecision,
    ReviewItem,
    ReviewRun,
)
from adminos.domain.decisions import RULE_ACTOR_PREFIX, ItemState
from adminos.logging import get_logger


GMAIL_THREAD = "gmail_thread"
GMAIL_DRAFT = "gmail_draft"
GMAIL_MESSAGE = "gmail_message"
DRAFT_SUBJECT_PREFIX = "Re: "

logger = get_logger(__name__)


class ActionError(RuntimeError):
    """Raised when an action operation is not valid."""


class ActionNotFound(ActionError):
    """Raised when an action does not exist on this run."""


class ActionRefused(ActionError):
    """Raised when permission, configuration, or state forbids the action."""


class ActionState(StrEnum):
    APPROVED = "approved"
    PREPARED = "prepared"
    EXECUTED = "executed"
    VERIFIED = "verified"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalKind(StrEnum):
    HUMAN = "human"
    AUTOMATABLE_RULE = "automatable_rule"


class ActionEventKind(StrEnum):
    APPROVED = "approved"
    PREPARED = "prepared"
    EXECUTION_STARTED = "execution_started"
    EXECUTED = "executed"
    ADOPTED_EXISTING = "adopted_existing"
    ALREADY_APPLIED = "already_applied"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    FAILED = "failed"


TERMINAL_ACTION_STATES = {ActionState.COMPLETED}
EXECUTABLE_ACTION_STATES = {ActionState.PREPARED, ActionState.EXECUTED, ActionState.FAILED}


@dataclass(frozen=True)
class ExecutionOutcome:
    """What an external write produced."""

    external_kind: str
    external_ref: str | None
    detail: JsonObject
    already_applied: bool = False
    adopted: bool = False


@dataclass(frozen=True)
class VerificationOutcome:
    """What reading the effect back showed."""

    verified: bool
    detail: JsonObject


@dataclass(frozen=True)
class Executor:
    """The three deterministic halves of an action: plan, do, read back.

    `prepare` performs no I/O, so an action can be planned and inspected while
    Gmail writes are switched off.
    """

    prepare: Callable[[ReviewItem, JsonObject], JsonObject]
    execute: Callable[[GmailClient, ReviewAction, ReviewItem], Awaitable[ExecutionOutcome]]
    verify: Callable[[GmailClient, ReviewAction, ReviewItem], Awaitable[VerificationOutcome]]


def idempotency_key(item_id: str, action: ActionKind, params: JsonObject) -> str:
    """Identity of an intended effect: the same intent yields the same key."""
    payload = json.dumps(
        {"item": item_id, "action": action.value, "params": params or {}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def ensure_actions(
    session: Session,
    capability: CapabilityConfig,
    run: ReviewRun,
    items: Sequence[ReviewItem],
    now: datetime | None = None,
) -> list[ReviewAction]:
    """Record an action for every approved item that does not have one yet.

    Approval creates the intent; nothing here touches Gmail. Re-running is
    free: an action already recorded for the same intent is returned as it is.
    """
    moment = now or datetime.now(UTC)
    recorded: list[ReviewAction] = []

    for item in items:
        if item.state != ItemState.APPROVED or item.approved_action is None:
            continue
        action_kind = ActionKind(item.approved_action)
        params = item.approved_params or {}
        key = idempotency_key(item.id, action_kind, params)

        existing = session.execute(
            select(ReviewAction).where(ReviewAction.idempotency_key == key)
        ).scalar_one_or_none()
        if existing is not None:
            recorded.append(existing)
            continue

        approval_kind, approved_by = read_approval(session, item)
        action = ReviewAction(
            run_id=run.id,
            item_id=item.id,
            capability_key=capability.key,
            action_kind=action_kind.value,
            state=ActionState.APPROVED,
            params=params,
            idempotency_key=key,
            target_thread_id=item.source_thread_id,
            approval_kind=approval_kind.value,
            approved_by=approved_by,
            rule_id=item.rule_id if approval_kind is ApprovalKind.AUTOMATABLE_RULE else None,
        )
        session.add(action)
        session.flush()
        record_event(session, action, ActionEventKind.APPROVED, {"approved_by": approved_by})
        recorded.append(action)
        logger.info("action %s recorded for item %s at %s", action.id, item.id, moment)

    return recorded


def read_approval(session: Session, item: ReviewItem) -> tuple[ApprovalKind, str]:
    """Who authorised this item, taken from the decision that approved it."""
    decision = session.execute(
        select(ReviewDecision)
        .where(ReviewDecision.item_id == item.id)
        .order_by(ReviewDecision.created_at.desc(), ReviewDecision.id.desc())
    ).scalars().first()

    if decision is None:
        return ApprovalKind.HUMAN, "unknown"
    if decision.actor.startswith(RULE_ACTOR_PREFIX):
        return ApprovalKind.AUTOMATABLE_RULE, decision.actor
    return ApprovalKind.HUMAN, decision.actor


def authorise_send(
    session: Session,
    capability: CapabilityConfig,
    run: ReviewRun,
    item: ReviewItem,
    draft_id: str,
    draft_message_id: str,
    actor: str,
) -> ReviewAction:
    """Approve sending one exact draft.

    Deliberately not reachable from an ordinary decision. Sending is approval
    of a specific piece of text: the draft must be one this capability wrote
    and verified, and the caller must name both its id and the id of the
    message it contained when it was read.
    """
    if not capability.permits(ActionKind.GMAIL_SEND_DRAFT):
        raise ActionRefused(f"{capability.key!r} is not allowed to send mail.")
    if not capability.may_execute(ActionKind.GMAIL_SEND_DRAFT):
        raise ActionRefused(f"{capability.key!r} is not permitted to execute a send.")

    draft = read_completed_draft(session, item.id)
    if draft is None or draft.external_ref != draft_id:
        raise ActionRefused(
            f"Draft {draft_id!r} is not a verified draft Admin OS created for this thread."
        )
    recorded_message_id = (draft.verification or {}).get("message_id")
    if recorded_message_id != draft_message_id:
        raise ActionRefused(
            "That is not the draft that was reviewed: its message id does not match."
        )

    params: JsonObject = {"draft_id": draft_id, "draft_message_id": draft_message_id}
    key = idempotency_key(item.id, ActionKind.GMAIL_SEND_DRAFT, params)
    existing = session.execute(
        select(ReviewAction).where(ReviewAction.idempotency_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    action = ReviewAction(
        run_id=run.id,
        item_id=item.id,
        capability_key=capability.key,
        action_kind=ActionKind.GMAIL_SEND_DRAFT.value,
        state=ActionState.APPROVED,
        params=params,
        idempotency_key=key,
        target_thread_id=item.source_thread_id,
        approval_kind=ApprovalKind.HUMAN.value,
        approved_by=actor,
    )
    session.add(action)
    session.flush()
    record_event(session, action, ActionEventKind.APPROVED, {"draft_id": draft_id})
    return action


def check_executable(capability: CapabilityConfig, action: ReviewAction) -> ActionKind:
    """Re-check at every step that this action is still allowed.

    Permission is not a property of the approval: configuration may have
    changed since, and an action approved last week must not execute today if
    the capability no longer permits it.
    """
    kind = ActionKind(action.action_kind)
    if not capability.playbook.allows(PlaybookStep.PREPARE_ACTIONS):
        raise ActionRefused(f"{capability.key!r} does not prepare actions in its playbook.")
    if not capability.permits(kind):
        raise ActionRefused(f"{capability.key!r} is not allowed to {kind.value!r}.")
    if not capability.may_execute(kind):
        raise ActionRefused(
            f"{capability.key!r} may approve {kind.value!r} but is not permitted to "
            "execute it."
        )
    if kind not in EXECUTORS:
        raise ActionRefused(f"There is no executor for {kind.value!r}.")
    return kind


def prepare_action(
    session: Session,
    capability: CapabilityConfig,
    action: ReviewAction,
    now: datetime | None = None,
) -> ReviewAction:
    """Turn an approval into an exact, inspectable plan. Writes nothing."""
    kind = check_executable(capability, action)
    if action.state != ActionState.APPROVED:
        return action

    item = read_action_item(session, action)
    executor = EXECUTORS[kind]
    action.prepared_params = executor.prepare(item, action.params or {})
    action.state = ActionState.PREPARED
    action.prepared_at = now or datetime.now(UTC)
    session.flush()
    record_event(session, action, ActionEventKind.PREPARED, action.prepared_params)
    return action


async def execute_action(
    session: Session,
    client: GmailClient,
    capability: CapabilityConfig,
    action: ReviewAction,
    now: datetime | None = None,
) -> ReviewAction:
    """Perform a prepared action, read it back, and record what happened.

    Retrying is safe: an effect already present is verified rather than
    written again, so a failure after a successful Gmail call cannot produce a
    second label, archive, or draft.
    """
    moment = now or datetime.now(UTC)
    kind = check_executable(capability, action)

    if action.state in TERMINAL_ACTION_STATES:
        return action
    if action.state not in EXECUTABLE_ACTION_STATES:
        raise ActionRefused(
            f"Action {action.id} is {action.state} and must be prepared before it runs."
        )
    if kind in GMAIL_ACTIONS and not is_gmail_write_enabled():
        raise ActionRefused(
            "Gmail writes are disabled. Set GMAIL_WRITE_ENABLED=true to allow them."
        )
    if not capability.playbook.allows(PlaybookStep.EXECUTE_APPROVED):
        raise ActionRefused(f"{capability.key!r} has no execute_approved step.")

    item = read_action_item(session, action)
    executor = EXECUTORS[kind]
    action.attempts += 1
    record_event(session, action, ActionEventKind.EXECUTION_STARTED, {"attempt": action.attempts})

    try:
        if action.attempts > 1:
            already = await executor.verify(client, action, item)
            if already.verified:
                record_event(session, action, ActionEventKind.ALREADY_APPLIED, already.detail)
                return settle_verified(session, action, item, already, moment)

        outcome = await executor.execute(client, action, item)
    except GmailError as exc:
        return fail_action(session, action, item, str(exc), moment)
    except ActionRefused as exc:
        return fail_action(session, action, item, str(exc), moment)

    action.external_kind = outcome.external_kind
    action.external_ref = outcome.external_ref
    action.state = ActionState.EXECUTED
    action.executed_at = moment
    action.last_error = None
    session.flush()
    record_event(
        session,
        action,
        execution_event(outcome),
        outcome.detail,
        external_ref=outcome.external_ref,
    )

    if not capability.execution.require_verification:
        return settle_verified(
            session,
            action,
            item,
            VerificationOutcome(verified=True, detail={"verification": "not required"}),
            moment,
        )

    try:
        verification = await executor.verify(client, action, item)
    except GmailError as exc:
        return fail_action(session, action, item, f"Verification could not run: {exc}", moment)

    if not verification.verified:
        record_event(session, action, ActionEventKind.VERIFICATION_FAILED, verification.detail)
        action.verification = verification.detail
        return fail_action(
            session,
            action,
            item,
            "Gmail did not show the change after it was made.",
            moment,
        )

    return settle_verified(session, action, item, verification, moment)


def execution_event(outcome: ExecutionOutcome) -> ActionEventKind:
    """What the attempt turned out to be: a write, an adoption, or a no-op."""
    if outcome.already_applied:
        return ActionEventKind.ALREADY_APPLIED
    if outcome.adopted:
        return ActionEventKind.ADOPTED_EXISTING
    return ActionEventKind.EXECUTED


async def verify_action(
    session: Session,
    client: GmailClient,
    capability: CapabilityConfig,
    action: ReviewAction,
    now: datetime | None = None,
) -> ReviewAction:
    """Read the effect back again, without writing anything."""
    moment = now or datetime.now(UTC)
    kind = ActionKind(action.action_kind)
    if kind not in EXECUTORS:
        raise ActionRefused(f"There is no executor for {kind.value!r}.")
    if action.state == ActionState.APPROVED or action.state == ActionState.PREPARED:
        raise ActionRefused(f"Action {action.id} has not run yet, so there is nothing to verify.")

    item = read_action_item(session, action)
    try:
        verification = await EXECUTORS[kind].verify(client, action, item)
    except GmailError as exc:
        return fail_action(session, action, item, f"Verification could not run: {exc}", moment)

    if not verification.verified:
        record_event(session, action, ActionEventKind.VERIFICATION_FAILED, verification.detail)
        action.verification = verification.detail
        return fail_action(session, action, item, "Gmail no longer shows the change.", moment)
    return settle_verified(session, action, item, verification, moment)


def settle_verified(
    session: Session,
    action: ReviewAction,
    item: ReviewItem,
    verification: VerificationOutcome,
    now: datetime,
) -> ReviewAction:
    action.verification = verification.detail
    action.state = ActionState.VERIFIED
    action.verified_at = now
    action.last_error = None
    action.failed_at = None
    session.flush()
    record_event(session, action, ActionEventKind.VERIFIED, verification.detail)

    action.state = ActionState.COMPLETED
    action.completed_at = now
    item.state = ItemState.EXECUTED
    session.flush()
    return action


def fail_action(
    session: Session,
    action: ReviewAction,
    item: ReviewItem,
    error: str,
    now: datetime,
) -> ReviewAction:
    """Record a durable failure. The action stays retryable."""
    action.state = ActionState.FAILED
    action.failed_at = now
    action.last_error = error
    item.state = ItemState.FAILED
    session.flush()
    record_event(session, action, ActionEventKind.FAILED, {"error": error})
    logger.warning("action %s failed on attempt %s", action.id, action.attempts)
    return action


def record_event(
    session: Session,
    action: ReviewAction,
    event: ActionEventKind,
    detail: JsonObject | None = None,
    external_ref: str | None = None,
) -> ActionEvent:
    """Append to the action's audit history."""
    sequence = (
        session.execute(
            select(ActionEvent.sequence)
            .where(ActionEvent.action_id == action.id)
            .order_by(ActionEvent.sequence.desc())
        ).scalars().first()
        or 0
    ) + 1

    record = ActionEvent(
        action_id=action.id,
        sequence=sequence,
        event=event.value,
        state_after=action.state,
        detail=detail,
        external_ref=external_ref or action.external_ref,
    )
    session.add(record)
    session.flush()
    return record


def read_action_item(session: Session, action: ReviewAction) -> ReviewItem:
    item = session.get(ReviewItem, action.item_id)
    if item is None:
        raise ActionNotFound(f"Action {action.id} has no review item.")
    return item


def read_action(session: Session, run: ReviewRun, action_id: str) -> ReviewAction:
    action = session.get(ReviewAction, action_id)
    if action is None or action.run_id != run.id:
        raise ActionNotFound(f"Run {run.id!r} has no action {action_id!r}.")
    return action


def read_actions(
    session: Session,
    run: ReviewRun,
    capability_key: str | None = None,
    states: Sequence[ActionState] | None = None,
) -> list[ReviewAction]:
    query = (
        select(ReviewAction)
        .where(ReviewAction.run_id == run.id)
        .order_by(ReviewAction.created_at, ReviewAction.id)
    )
    if capability_key is not None:
        query = query.where(ReviewAction.capability_key == capability_key)
    if states is not None:
        query = query.where(ReviewAction.state.in_([state.value for state in states]))
    return list(session.execute(query).scalars().all())


def read_action_events(session: Session, action: ReviewAction) -> list[ActionEvent]:
    return list(
        session.execute(
            select(ActionEvent)
            .where(ActionEvent.action_id == action.id)
            .order_by(ActionEvent.sequence)
        )
        .scalars()
        .all()
    )


def read_completed_draft(session: Session, item_id: str) -> ReviewAction | None:
    """The completed draft on an item, which is the only thing that may be sent."""
    return (
        session.execute(
            select(ReviewAction)
            .where(
                ReviewAction.item_id == item_id,
                ReviewAction.action_kind == ActionKind.GMAIL_DRAFT_REPLY.value,
                ReviewAction.state == ActionState.COMPLETED.value,
            )
            .order_by(ReviewAction.completed_at.desc())
        )
        .scalars()
        .first()
    )


def read_strings(params: JsonObject, key: str) -> list[str]:
    value = params.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [entry.strip() for entry in value if isinstance(entry, str) and entry.strip()]
    raise ActionRefused(f"{key!r} must be a string or a list of strings.")


def read_text(params: JsonObject, key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ActionRefused(f"{key!r} must be a non-empty string.")
    return value.strip()


def prepare_label(item: ReviewItem, params: JsonObject) -> JsonObject:
    add = read_strings(params, "add_labels")
    remove = read_strings(params, "remove_labels")
    if not add and not remove:
        raise ActionRefused("A label action must add or remove at least one label.")
    if INBOX_LABEL_ID in {name.upper() for name in add + remove}:
        raise ActionRefused(
            "INBOX is not a label to set by hand; archive is its own action, so that "
            "leaving the inbox is always recorded as an archive."
        )
    return {"add_labels": add, "remove_labels": remove}


async def execute_label(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> ExecutionOutcome:
    add_ids, remove_ids = await resolve_labels(client, action)
    await client.modify_thread(item.source_thread_id, add_ids, remove_ids)
    return ExecutionOutcome(
        external_kind=GMAIL_THREAD,
        external_ref=item.source_thread_id,
        detail={"added": add_ids, "removed": remove_ids},
    )


async def verify_label(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> VerificationOutcome:
    add_ids, remove_ids = await resolve_labels(client, action)
    thread = await client.fetch_thread(item.source_thread_id)
    present = set(thread.label_ids)
    missing = [label_id for label_id in add_ids if label_id not in present]
    lingering = [label_id for label_id in remove_ids if label_id in present]
    return VerificationOutcome(
        verified=not missing and not lingering,
        detail={"missing": missing, "lingering": lingering, "labels": thread.label_ids},
    )


async def resolve_labels(
    client: GmailClient,
    action: ReviewAction,
) -> tuple[list[str], list[str]]:
    """Turn label names into ids, refusing to invent a label that is not there."""
    prepared = action.prepared_params or {}
    resolved: dict[str, list[str]] = {"add_labels": [], "remove_labels": []}
    for key in resolved:
        for name in read_strings(prepared, key):
            label_id = await client.resolve_label_id(name)
            if label_id is None:
                raise ActionRefused(f"The mailbox has no label named {name!r}.")
            resolved[key].append(label_id)
    return resolved["add_labels"], resolved["remove_labels"]


def prepare_archive(item: ReviewItem, params: JsonObject) -> JsonObject:
    return {"remove_labels": [INBOX_LABEL_ID], "thread_id": item.source_thread_id}


async def execute_archive(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> ExecutionOutcome:
    thread = await client.fetch_thread(item.source_thread_id)
    if INBOX_LABEL_ID not in thread.label_ids:
        return ExecutionOutcome(
            external_kind=GMAIL_THREAD,
            external_ref=item.source_thread_id,
            detail={"labels": thread.label_ids, "already": "out of the inbox"},
            already_applied=True,
        )

    await client.modify_thread(item.source_thread_id, remove_label_ids=[INBOX_LABEL_ID])
    return ExecutionOutcome(
        external_kind=GMAIL_THREAD,
        external_ref=item.source_thread_id,
        detail={"removed": [INBOX_LABEL_ID]},
    )


async def verify_archive(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> VerificationOutcome:
    thread = await client.fetch_thread(item.source_thread_id)
    return VerificationOutcome(
        verified=INBOX_LABEL_ID not in thread.label_ids,
        detail={"labels": thread.label_ids},
    )


def prepare_trash(item: ReviewItem, params: JsonObject) -> JsonObject:
    """Plan a move to Trash, which is where a deleted thread goes.

    `permanent: false` is written into the plan rather than assumed, so what
    was approved is legible in the audit years later: this moves the thread,
    it does not destroy it.
    """
    return {
        "thread_id": item.source_thread_id,
        "moves_to": TRASH_LABEL_ID,
        "permanent": False,
    }


async def execute_trash(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> ExecutionOutcome:
    thread = await client.fetch_thread(item.source_thread_id)
    if TRASH_LABEL_ID in thread.label_ids:
        return ExecutionOutcome(
            external_kind=GMAIL_THREAD,
            external_ref=item.source_thread_id,
            detail={"labels": thread.label_ids, "already": "in Trash"},
            already_applied=True,
        )

    await client.trash_thread(item.source_thread_id)
    return ExecutionOutcome(
        external_kind=GMAIL_THREAD,
        external_ref=item.source_thread_id,
        detail={"moved_to": TRASH_LABEL_ID, "permanent": False},
    )


async def verify_trash(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> VerificationOutcome:
    """Read the thread back and insist Gmail itself says it is in Trash."""
    thread = await client.fetch_thread(item.source_thread_id)
    return VerificationOutcome(
        verified=TRASH_LABEL_ID in thread.label_ids,
        detail={"labels": thread.label_ids},
    )


def prepare_draft(item: ReviewItem, params: JsonObject) -> JsonObject:
    recipients = read_strings(params, "to")
    if not recipients:
        raise ActionRefused("A draft must name at least one recipient.")
    body = read_text(params, "body")
    if body is None:
        raise ActionRefused("A draft must have a body; nothing is written on your behalf.")
    subject = read_text(params, "subject") or f"{DRAFT_SUBJECT_PREFIX}{item.subject or ''}".strip()
    return {
        "to": recipients,
        "cc": read_strings(params, "cc"),
        "subject": subject,
        "body": body,
        "thread_id": item.source_thread_id,
    }


async def execute_draft(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> ExecutionOutcome:
    if action.attempts > 1 and action.external_ref is None:
        existing = await client.find_draft_for_thread(item.source_thread_id)
        if existing is not None:
            return ExecutionOutcome(
                external_kind=GMAIL_DRAFT,
                external_ref=existing.draft_id,
                detail={"message_id": existing.message_id},
                adopted=True,
            )

    prepared = action.prepared_params or {}
    body = read_text(prepared, "body")
    subject = read_text(prepared, "subject")
    if body is None or subject is None:
        raise ActionRefused("The prepared draft is incomplete.")

    draft = await client.create_draft(
        thread_id=item.source_thread_id,
        to=read_strings(prepared, "to"),
        subject=subject,
        body=body,
        cc=read_strings(prepared, "cc"),
    )
    return ExecutionOutcome(
        external_kind=GMAIL_DRAFT,
        external_ref=draft.draft_id,
        detail={"message_id": draft.message_id, "thread_id": draft.thread_id},
    )


async def verify_draft(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> VerificationOutcome:
    if action.external_ref is None:
        return VerificationOutcome(verified=False, detail={"draft": "not created"})
    draft = await client.fetch_draft(action.external_ref)
    if draft is None:
        return VerificationOutcome(verified=False, detail={"draft": "missing"})
    return VerificationOutcome(
        verified=True,
        detail={"draft_id": draft.draft_id, "message_id": draft.message_id, "sent": False},
    )


def prepare_send(item: ReviewItem, params: JsonObject) -> JsonObject:
    draft_id = read_text(params, "draft_id")
    message_id = read_text(params, "draft_message_id")
    if draft_id is None or message_id is None:
        raise ActionRefused(
            "Sending needs the exact draft: give both draft_id and draft_message_id "
            "from the draft that was reviewed."
        )
    return {"draft_id": draft_id, "draft_message_id": message_id}


async def execute_send(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> ExecutionOutcome:
    prepared = action.prepared_params or {}
    draft_id = read_text(prepared, "draft_id")
    approved_message_id = read_text(prepared, "draft_message_id")
    if draft_id is None or approved_message_id is None:
        raise ActionRefused("The prepared send is incomplete.")

    draft = await client.fetch_draft(draft_id)
    if draft is None:
        raise ActionRefused(f"Draft {draft_id!r} no longer exists, so it cannot be sent.")
    if draft.message_id != approved_message_id:
        raise ActionRefused(
            "The draft has changed since it was approved. Review the new text and "
            "approve that draft instead."
        )

    message_id = await client.send_draft(draft_id)
    return ExecutionOutcome(
        external_kind=GMAIL_MESSAGE,
        external_ref=message_id,
        detail={"draft_id": draft_id, "sent_message_id": message_id},
    )


async def verify_send(
    client: GmailClient,
    action: ReviewAction,
    item: ReviewItem,
) -> VerificationOutcome:
    prepared = action.prepared_params or {}
    draft_id = read_text(prepared, "draft_id")
    if draft_id is None:
        return VerificationOutcome(verified=False, detail={"draft": "unknown"})
    draft = await client.fetch_draft(draft_id)
    return VerificationOutcome(
        verified=draft is None,
        detail={"draft_id": draft_id, "draft_still_present": draft is not None},
    )


EXECUTORS: dict[ActionKind, Executor] = {
    ActionKind.GMAIL_LABEL: Executor(prepare_label, execute_label, verify_label),
    ActionKind.GMAIL_ARCHIVE: Executor(prepare_archive, execute_archive, verify_archive),
    ActionKind.GMAIL_TRASH: Executor(prepare_trash, execute_trash, verify_trash),
    ActionKind.GMAIL_DRAFT_REPLY: Executor(prepare_draft, execute_draft, verify_draft),
    ActionKind.GMAIL_SEND_DRAFT: Executor(prepare_send, execute_send, verify_send),
}
"""Every action Admin OS can perform.

Permanent deletion is deliberately absent. `gmail.trash` moves a thread to
Trash, from which Gmail restores it; nothing here calls `threads.delete` or
`messages.delete`, and an action with no executor cannot run whatever a
capability or a caller asks for.
"""
