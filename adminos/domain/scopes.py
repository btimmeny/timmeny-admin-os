"""What an execution is allowed to touch, and nothing else.

Deciding, preparing, and executing are three separate requests. Between them
the selection has to survive intact: nineteen rows chosen must be nineteen
rows prepared and nineteen rows executed. Nothing here infers a scope from a
capability, because "everything in Admin" is a different sentence from "these
nineteen" and only one of them was said.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.db.models import ActionScope, ReviewAction, ReviewRun
from adminos.logging import get_logger


logger = get_logger(__name__)


class ScopeError(RuntimeError):
    """Raised when a scope is missing, stale, or does not match."""


class ScopeNotFound(ScopeError):
    """Raised when a scope does not exist on this run."""


class ScopeMismatch(ScopeError):
    """Raised when what would run is not what was prepared and selected.

    Carries the difference rather than a bare refusal: the useful answer to
    "these are not the same nineteen" names which rows differ.
    """

    def __init__(self, message: str, detail: dict[str, list[str] | str]) -> None:
        self.detail: dict[str, list[str] | str] = {"error": "ScopeMismatch", **detail}
        self.detail["message"] = message
        super().__init__(message)


class ScopeState(StrEnum):
    """A scope is current until it is executed or replaced by a newer one."""

    CURRENT = "current"
    EXECUTED = "executed"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class ExcludedItem:
    """One selected item that was not prepared, and why not."""

    item_id: str
    reason: str


def open_scope(
    session: Session,
    run: ReviewRun,
    capability_key: str | None,
    entire_capability: bool,
    requested_item_ids: Sequence[str],
    actions: Sequence[ReviewAction],
    excluded: Sequence[ExcludedItem],
    actor: str,
    now: datetime | None = None,
) -> ActionScope:
    """Record what was just prepared, superseding any earlier preparation.

    Preparing again is how a selection is changed, so the previous scope stops
    being executable at that moment: a confirmation given for an older list
    must not run the newer one, or the other way round.
    """
    moment = now or datetime.now(UTC)
    supersede_open_scopes(session, run, capability_key, moment)

    scope = ActionScope(
        run_id=run.id,
        capability_key=capability_key,
        state=ScopeState.CURRENT.value,
        entire_capability=entire_capability,
        requested_item_ids=list(requested_item_ids),
        prepared_item_ids=[action.item_id for action in actions],
        action_ids=[action.id for action in actions],
        excluded=[{"item_id": entry.item_id, "reason": entry.reason} for entry in excluded],
        actor=actor,
        created_at=moment,
    )
    session.add(scope)
    session.flush()
    logger.info(
        "scope %s prepared %s actions on run %s", scope.id, len(scope.action_ids), run.id
    )
    return scope


def supersede_open_scopes(
    session: Session,
    run: ReviewRun,
    capability_key: str | None,
    now: datetime,
    keep: str | None = None,
) -> None:
    for scope in read_open_scopes(session, run, capability_key):
        if scope.id == keep:
            continue
        scope.state = ScopeState.SUPERSEDED.value
        scope.superseded_by = keep
    session.flush()


def read_open_scopes(
    session: Session,
    run: ReviewRun,
    capability_key: str | None,
) -> list[ActionScope]:
    query = select(ActionScope).where(
        ActionScope.run_id == run.id,
        ActionScope.state == ScopeState.CURRENT.value,
    )
    if capability_key is not None:
        query = query.where(ActionScope.capability_key == capability_key)
    return list(session.execute(query).scalars().all())


def read_scope(session: Session, run: ReviewRun, scope_id: str) -> ActionScope:
    scope = session.get(ActionScope, scope_id)
    if scope is None or scope.run_id != run.id:
        raise ScopeNotFound(f"Run {run.id!r} has no prepared scope {scope_id!r}.")
    return scope


def check_scope_current(scope: ActionScope) -> None:
    """Refuse a preparation that has been executed or replaced since."""
    if scope.state == ScopeState.SUPERSEDED.value:
        raise ScopeMismatch(
            f"Scope {scope.id} was superseded by a later preparation, so the "
            "confirmation no longer describes what would run. Prepare again.",
            {"scope_id": scope.id, "state": scope.state},
        )
    if scope.state == ScopeState.EXECUTED.value:
        raise ScopeMismatch(
            f"Scope {scope.id} has already been executed. Prepare again to run "
            "anything further.",
            {"scope_id": scope.id, "state": scope.state},
        )


def check_scope_matches(scope: ActionScope, item_ids: Sequence[str]) -> None:
    """Refuse when the caller's latest selection is not what was prepared.

    The caller restates what it believes it is confirming. If that differs
    from the prepared scope in either direction, the disagreement is the whole
    point: it is surfaced instead of resolved.
    """
    prepared = set(scope.prepared_item_ids)
    requested = set(item_ids)
    if prepared == requested:
        return
    raise ScopeMismatch(
        "The selection being confirmed is not the one that was prepared, so "
        "nothing was executed.",
        {
            "scope_id": scope.id,
            "prepared_item_ids": sorted(prepared),
            "requested_item_ids": sorted(requested),
            "not_prepared": sorted(requested - prepared),
            "prepared_but_not_requested": sorted(prepared - requested),
        },
    )


def check_scope_invariant(scope: ActionScope, actions: Sequence[ReviewAction]) -> None:
    """The items decided, prepared, and about to run must be one set.

    Checked before anything is written, because the failure this exists to
    catch — a narrow selection widening into a whole capability — is only
    worth catching before the mailbox changes.
    """
    prepared = set(scope.prepared_item_ids)
    about_to_run = {action.item_id for action in actions}
    if prepared == about_to_run:
        return
    raise ScopeMismatch(
        "What would run is not what was prepared, so nothing was executed.",
        {
            "scope_id": scope.id,
            "prepared_item_ids": sorted(prepared),
            "would_execute_item_ids": sorted(about_to_run),
            "not_prepared": sorted(about_to_run - prepared),
            "missing": sorted(prepared - about_to_run),
        },
    )


def settle_scope(
    session: Session,
    scope: ActionScope,
    executed: Sequence[ReviewAction],
    verified: Sequence[ReviewAction],
    now: datetime | None = None,
) -> ActionScope:
    """Record what the execution actually touched, and close the scope.

    A verified set smaller than the prepared one is a failure to report, not a
    scope violation: those actions stay visible and retryable. A set *larger*
    than prepared cannot happen without a bug, and is recorded as such.
    """
    scope.executed_item_ids = sorted({action.item_id for action in executed})
    scope.verified_item_ids = sorted({action.item_id for action in verified})
    scope.state = ScopeState.EXECUTED.value
    scope.executed_at = now or datetime.now(UTC)
    session.flush()

    unexpected = set(scope.executed_item_ids) - set(scope.prepared_item_ids)
    if unexpected:
        logger.error(
            "scope %s executed items it did not prepare: %s", scope.id, sorted(unexpected)
        )
    return scope
