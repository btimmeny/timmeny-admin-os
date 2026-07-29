from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.capabilities.config import ActionKind, LoadedCapabilities
from adminos.db.models import ReviewAction, ReviewItem, ReviewPlan, ReviewRun
from adminos.domain.actions import ActionState
from adminos.domain.decisions import HUMAN_ACTOR, ItemState
from adminos.logging import get_logger


logger = get_logger(__name__)


class PlanStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"


class OpeningMode(StrEnum):
    """Whether a review is being entered for the first time or picked up.

    The two are told apart because they are promised different things: one
    morning is being laid out, the other carried on with.
    """

    NEW = "new"
    RESUMED = "resumed"


class PlanRefused(RuntimeError):
    """Raised when a plan cannot be worked in the way it was asked for."""


def plan_for(
    session: Session,
    loaded: LoadedCapabilities,
    run: ReviewRun,
    begun: bool = False,
    now: datetime | None = None,
) -> ReviewPlan:
    """This review's plan, written the first time the review is read.

    A review already under way is given an `active` plan: it was begun by
    somebody, and asking to begin a morning half worked would be a fiction
    told to satisfy a new field.
    """
    plan = session.execute(
        select(ReviewPlan).where(ReviewPlan.run_id == run.id)
    ).scalar_one_or_none()
    if plan is not None:
        return plan

    moment = now or datetime.now(UTC)
    plan = ReviewPlan(
        run_id=run.id,
        status=PlanStatus.ACTIVE if begun else PlanStatus.PROPOSED,
        version=1,
        sequence=[capability.key for capability in loaded.enabled()],
        skipped=[],
        config_version=loaded.version,
        begun_at=moment if begun else None,
        begun_by=HUMAN_ACTOR if begun else None,
    )
    session.add(plan)
    session.flush()
    logger.info("review %s planned as %s", run.id, plan.status)
    return plan


def activate_plan(
    session: Session,
    run: ReviewRun,
    actor: str = HUMAN_ACTOR,
    now: datetime | None = None,
) -> ReviewPlan | None:
    """Treat working the review as agreeing to its plan.

    Deciding a row is a stronger statement than "begin": it is the review
    under way. Asking afterwards whether to begin would be a question whose
    answer has already been given.
    """
    plan = session.execute(
        select(ReviewPlan).where(ReviewPlan.run_id == run.id)
    ).scalar_one_or_none()
    if plan is None or plan.status == PlanStatus.ACTIVE:
        return plan

    plan.status = PlanStatus.ACTIVE
    plan.begun_at = now or datetime.now(UTC)
    plan.begun_by = actor
    session.flush()
    return plan


def begin_plan(
    session: Session,
    loaded: LoadedCapabilities,
    run: ReviewRun,
    order: Sequence[str] | None = None,
    only: Sequence[str] | None = None,
    skip: Sequence[str] | None = None,
    actor: str = HUMAN_ACTOR,
    now: datetime | None = None,
) -> ReviewPlan:
    """Agree the plan, in the order and with the omissions Brian asked for.

    Saying which groups to work is a change to this review and to nothing
    else: it is recorded as a version of this plan rather than as a change to
    the configuration, so "skip Legal today" cannot quietly become "Legal is
    not reviewed".
    """
    moment = now or datetime.now(UTC)
    plan = plan_for(session, loaded, run, begun=True, now=moment)
    configured = [capability.key for capability in loaded.enabled()]

    sequence = arrange(configured, order)
    omitted = set_aside(configured, sequence, only, skip)
    if set(sequence) - omitted == set():
        raise PlanRefused("A review with every group set aside has nothing to work.")

    changed = list(plan.sequence) != sequence or set(plan.skipped) != omitted
    plan.sequence = sequence
    plan.skipped = sorted(omitted)
    plan.config_version = loaded.version
    if changed:
        plan.version += 1
    if plan.status != PlanStatus.ACTIVE:
        plan.status = PlanStatus.ACTIVE
        plan.begun_at = moment
        plan.begun_by = actor
    session.flush()
    logger.info(
        "review %s plan begun at version %s, working %s",
        run.id,
        plan.version,
        ", ".join(key for key in sequence if key not in omitted),
    )
    return plan


def arrange(configured: Sequence[str], order: Sequence[str] | None) -> list[str]:
    """The whole order, with the groups Brian named brought to the front.

    Naming two of five groups is "do these first", not "these are the only
    ones": the rest follow in their configured order, and dropping a group
    from the review is `only` or `skip`, which say so.
    """
    if not order:
        return list(configured)

    check_known(configured, order)
    named = list(dict.fromkeys(order))
    return named + [key for key in configured if key not in named]


def set_aside(
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
        raise PlanRefused(
            f"No group named {', '.join(repr(key) for key in unknown)} is in this "
            f"review. It works {', '.join(repr(key) for key in configured)}."
        )


@dataclass(frozen=True)
class ReviewStanding:
    """What a review owes, in the three ways it can owe it.

    The three are disjoint: a row is counted where it has got to and nowhere
    else, so adding them gives the number of rows between a decision and a
    verified effect on the mailbox.
    """

    decided_not_executed: int
    prepared_awaiting_confirmation: int
    failed_or_unverified: int

    def outstanding(self) -> int:
        return (
            self.decided_not_executed
            + self.prepared_awaiting_confirmation
            + self.failed_or_unverified
        )


ACTION_NAMES: dict[str, str] = {
    ActionKind.GMAIL_ARCHIVE: "archived",
    ActionKind.GMAIL_MOVE: "filed",
    ActionKind.GMAIL_TRASH: "moved_to_trash",
    ActionKind.GMAIL_UNTRASH: "restored_from_trash",
    ActionKind.GMAIL_LABEL: "labelled",
    ActionKind.GMAIL_DRAFT_REPLY: "replies_drafted",
    ActionKind.GMAIL_SEND_DRAFT: "replies_sent",
    ActionKind.MONDAY_CREATE_TASK: "tasks_created",
}
"""What each action is called when counting what a review did.

Named in the past tense on purpose: every one of these is counted from a
completed, verified action, so the word is only ever earned.
"""


@dataclass(frozen=True)
class ReviewSummary:
    """What a review did, counted from verified execution.

    Nothing here is read from a decision: `moved_to_trash` is the number of
    threads Gmail confirmed are in Trash, not the number Brian said should be.
    """

    reviewed: int
    """Rows Brian has settled one way or another, not rows loaded."""

    done: dict[str, int]
    deferred: int
    dismissed: int
    standing: ReviewStanding
    rule_matched: int


NOTHING_OWED = ReviewStanding(
    decided_not_executed=0, prepared_awaiting_confirmation=0, failed_or_unverified=0
)


ATTEMPTED_ACTION_STATES = {
    ActionState.PREPARED,
    ActionState.EXECUTED,
    ActionState.VERIFIED,
    ActionState.FAILED,
}
"""Actions that have gone beyond a decision without finishing.

`executed` and `verified` are in here with `failed`: an action is only done
when it has been read back from Gmail and settled as completed, and the states
between are exactly the ones worth reporting as unfinished.
"""


def count_standing(
    items: Sequence[ReviewItem], actions: Sequence[ReviewAction]
) -> ReviewStanding:
    """Where a set of rows has got to, in the three ways it can be short.

    An action only exists once a decision has been prepared, so a row decided
    this morning and left there is counted from the row itself. Counting it
    from actions would report nothing owed, which is the mistake this whole
    increment exists to stop being possible.
    """
    states = [ActionState(action.state) for action in actions]
    carried = {
        action.item_id
        for action in actions
        if ActionState(action.state) in ATTEMPTED_ACTION_STATES
    }
    return ReviewStanding(
        decided_not_executed=sum(
            1
            for item in items
            if item.state in {ItemState.APPROVED, ItemState.FAILED} and item.id not in carried
        ),
        prepared_awaiting_confirmation=sum(
            1 for state in states if state is ActionState.PREPARED
        ),
        failed_or_unverified=sum(
            1
            for state in states
            if state in {ActionState.FAILED, ActionState.EXECUTED, ActionState.VERIFIED}
        ),
    )


def actions_by_capability(
    session: Session, run: ReviewRun
) -> dict[str, list[ReviewAction]]:
    grouped: dict[str, list[ReviewAction]] = {}
    for action in read_run_actions(session, run):
        grouped.setdefault(action.capability_key, []).append(action)
    return grouped


def read_run_actions(session: Session, run: ReviewRun) -> list[ReviewAction]:
    return list(
        session.execute(select(ReviewAction).where(ReviewAction.run_id == run.id))
        .scalars()
        .all()
    )


def read_run_items(session: Session, run: ReviewRun) -> list[ReviewItem]:
    return list(
        session.execute(select(ReviewItem).where(ReviewItem.run_id == run.id))
        .scalars()
        .all()
    )


def review_summary(session: Session, run: ReviewRun) -> ReviewSummary:
    """What this review has actually done, and what it still owes."""
    items = read_run_items(session, run)
    actions = read_run_actions(session, run)

    done: dict[str, int] = {}
    for action in actions:
        if ActionState(action.state) is not ActionState.COMPLETED:
            continue
        name = ACTION_NAMES.get(action.action_kind, action.action_kind)
        done[name] = done.get(name, 0) + 1

    return ReviewSummary(
        reviewed=sum(1 for item in items if item.state != ItemState.PENDING),
        done=done,
        deferred=sum(1 for item in items if item.state == ItemState.DEFERRED),
        dismissed=sum(1 for item in items if item.state == ItemState.DISMISSED),
        standing=count_standing(items, actions),
        rule_matched=sum(1 for item in items if item.rule_id is not None),
    )
