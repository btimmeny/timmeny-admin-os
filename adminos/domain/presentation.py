"""Turning a review group into the screen its contract describes.

Admin OS decides what the reader sees. This module reads the capability's
presentation contract and produces the finished cells, in the contract's own
column order, together with the exact request the reader's answer should
become. A renderer needs no knowledge of Gmail, of confidence floors, or of
which decisions this capability accepts.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from adminos.capabilities.config import (
    ACTION_VALUES,
    DESTINATION_PARAM,
    ActionKind,
    CapabilityConfig,
    Recommendation,
)
from adminos.capabilities.screens import (
    ColumnFormat,
    ColumnSource,
    RowScope,
    ScreenAction,
    ScreenColumn,
    ScreenConfig,
)
from adminos.db.models import JsonObject, ReviewItem, ReviewRun
from adminos.domain.decisions import ItemState
from adminos.domain.review import (
    OPEN_ITEM_STATES,
    DecisionRefused,
    GroupView,
    check_decision,
)


ELLIPSIS = "…"

DEFAULT_ACTION_LABELS: dict[str, str] = {
    ActionKind.GMAIL_LABEL: "Re-label it",
    ActionKind.GMAIL_ARCHIVE: "Archive it",
    ActionKind.GMAIL_MOVE: "File it",
    ActionKind.GMAIL_TRASH: "Move it to Trash",
    ActionKind.GMAIL_DRAFT_REPLY: "Draft a reply",
    ActionKind.GMAIL_SEND_DRAFT: "Send the approved draft",
    ActionKind.MONDAY_CREATE_TASK: "Create a Monday task",
    Recommendation.NEEDS_REVIEW: "Needs review",
    Recommendation.NO_ACTION: "Nothing to do",
}
"""Read when a screen does not name its own wording for an outcome."""

STATE_LABELS: dict[str, str] = {
    ItemState.PENDING: "Pending",
    ItemState.APPROVED: "Approved",
    ItemState.EXECUTED: "Done",
    ItemState.FAILED: "Failed",
    ItemState.DISMISSED: "Dismissed",
    ItemState.DEFERRED: "Deferred",
}

ITEM_DECISION_PATH = "/review/runs/{run_id}/items/{{item_id}}/decision"
GROUP_DECISION_PATH = "/review/runs/{run_id}/groups/{capability_key}/decisions"


@dataclass(frozen=True)
class RenderedColumn:
    key: str
    label: str
    align: str
    format: str


@dataclass(frozen=True)
class RenderedParam:
    """Something an action needs to be said before it can be taken.

    `choices` is exhaustive where it is given: a move may only name one of
    this capability's folders, so the reader picks rather than types.
    """

    name: str
    label: str
    required: bool
    choices: list[str]


@dataclass(frozen=True)
class RenderedAction:
    """One offered decision, and the request that sends it."""

    id: str
    label: str
    decision: str
    action: str | None
    scope: str
    method: str
    path: str
    body: dict[str, str]
    params: list[RenderedParam] = field(default_factory=list)


@dataclass(frozen=True)
class RenderedRow:
    """One line of the table: finished cells, in column order."""

    item_id: str
    thread_id: str
    cells: list[str]
    actions: list[str]


@dataclass(frozen=True)
class ScreenView:
    """A presentation contract and the rows it describes."""

    screen_id: str
    kind: str
    title: str
    columns: list[RenderedColumn]
    actions: list[RenderedAction]
    rows: list[RenderedRow] = field(default_factory=list)
    footer: str = ""
    empty_text: str = ""


def render_group(
    screen: ScreenConfig,
    view: GroupView,
    run: ReviewRun,
    now: datetime | None = None,
) -> ScreenView:
    """Build the screen for one capability group."""
    moment = now or datetime.now(UTC)
    items = sort_items(screen, shown_items(screen, view.items))
    labels = {**DEFAULT_ACTION_LABELS, **screen.action_labels}

    rows = [
        RenderedRow(
            item_id=item.id,
            thread_id=item.source_thread_id,
            cells=[
                render_cell(column, index, item, view.capability, labels, moment)
                for column in screen.columns
            ],
            actions=available_actions(screen, view.capability, item),
        )
        for index, item in enumerate(items, start=1)
    ]

    return ScreenView(
        screen_id=screen.id,
        kind=screen.kind,
        title=screen.title,
        columns=[
            RenderedColumn(
                key=column.source.value,
                label=column.label,
                align=column.align,
                format=column.resolved_format().value,
            )
            for column in screen.columns
        ],
        actions=[
            render_action(offered, run, view.capability)
            for offered in screen.actions
            if offers_anything(offered, view.capability)
        ],
        rows=rows,
        footer=render_footer(screen, view, run),
        empty_text=screen.empty_text,
    )


def shown_items(screen: ScreenConfig, items: list[ReviewItem]) -> list[ReviewItem]:
    """The items this contract puts on the table.

    A thread that has been archived, trashed, dismissed, or put off is done
    with for today: leaving it in the table would invite deciding it twice.
    The footer still counts it, and its action history is untouched.
    """
    if screen.rows is RowScope.ALL:
        return list(items)
    return [item for item in items if item.state in OPEN_ITEM_STATES]


def sort_items(screen: ScreenConfig, items: list[ReviewItem]) -> list[ReviewItem]:
    """Order rows as the contract asks, most significant key last."""
    ordered = list(items)
    for rule in reversed(screen.sort):
        descending = rule.direction == "desc"
        ordered.sort(
            key=lambda item, source=rule.source, desc=descending: sort_key(source, item, desc),
            reverse=descending,
        )
    return ordered


def sort_key(source: ColumnSource, item: ReviewItem, descending: bool) -> tuple[int, float | str]:
    """A comparable key that leaves rows with nothing to sort on at the end.

    The rank is inverted for a descending sort so that a thread with no date
    stays at the bottom either way, rather than leading the table because its
    value is missing.
    """
    present, missing = (1, 0) if descending else (0, 1)

    if source is ColumnSource.RECEIVED:
        if item.received_at is None:
            return (missing, 0.0)
        received_at = item.received_at
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=UTC)
        return (present, received_at.timestamp())
    if source is ColumnSource.CONFIDENCE:
        return (present, item.recommendation_confidence)
    if source is ColumnSource.SUBJECT:
        if not item.subject:
            return (missing, "")
        return (present, item.subject.casefold())
    return (present, item.state)


def render_cell(
    column: ScreenColumn,
    index: int,
    item: ReviewItem,
    capability: CapabilityConfig,
    labels: dict[str, str],
    now: datetime,
) -> str:
    value = read_source(column.source, index, item, capability, labels, now)
    text = format_value(column, value, now)
    if not text:
        return column.empty_text
    if column.max_chars is not None and len(text) > column.max_chars:
        return text[: column.max_chars - 1].rstrip() + ELLIPSIS
    return text


def read_source(
    source: ColumnSource,
    index: int,
    item: ReviewItem,
    capability: CapabilityConfig,
    labels: dict[str, str],
    now: datetime,
) -> object:
    match source:
        case ColumnSource.INDEX:
            return index
        case ColumnSource.GROUP:
            return capability.name
        case ColumnSource.WHAT_IT_IS:
            return what_it_is(item)
        case ColumnSource.KEY_FACTS:
            return key_facts(item, now)
        case ColumnSource.RECOMMENDED_ACTION:
            return recommended_action(item, labels)
        case ColumnSource.WHY:
            return item.recommendation_rationale
        case ColumnSource.CONFIDENCE:
            return item.recommendation_confidence
        case ColumnSource.DECISION:
            return decision_text(item, labels)
        case ColumnSource.SUBJECT:
            return item.subject
        case ColumnSource.PARTICIPANTS:
            return participants(item)
        case ColumnSource.RECEIVED:
            return item.received_at
        case ColumnSource.STATE:
            return STATE_LABELS.get(item.state, item.state)


def format_value(column: ScreenColumn, value: object, now: datetime) -> str:
    """Apply the column's format. Empty values become the column's placeholder."""
    if value is None:
        return ""

    match column.resolved_format():
        case ColumnFormat.NUMBER:
            return f"{value:g}" if isinstance(value, (int, float)) else str(value)
        case ColumnFormat.PERCENT:
            if not isinstance(value, (int, float)) or value <= 0:
                return ""
            return f"{round(value * 100)}%"
        case ColumnFormat.DATE:
            return value.strftime("%-d %b %Y") if isinstance(value, datetime) else str(value)
        case ColumnFormat.RELATIVE_DATE:
            return relative_age(value, now) if isinstance(value, datetime) else str(value)
        case ColumnFormat.JOINED:
            if isinstance(value, list):
                return ", ".join(str(entry) for entry in value)
            return str(value)
        case ColumnFormat.TEXT:
            return str(value)


def recommended_action(item: ReviewItem, labels: dict[str, str]) -> str:
    """What is recommended, said with its destination where it has one.

    "File it" is not a recommendation anyone can answer; "File it in
    Career/Citi" is.
    """
    text = labels.get(item.recommendation, item.recommendation)
    return with_destination(text, item.recommendation, item.recommendation_params)


def with_destination(text: str, action: str | None, params: JsonObject | None) -> str:
    if action != ActionKind.GMAIL_MOVE:
        return text
    destination = (params or {}).get(DESTINATION_PARAM)
    return f"{text} in {destination}" if isinstance(destination, str) else text


def what_it_is(item: ReviewItem) -> str:
    """The subject, said as the kind of thing it is when that is known."""
    subject = (item.subject or "").strip()
    if item.category:
        kind = item.category.replace("_", " ")
        return f"{kind.capitalize()}: {subject}" if subject else kind.capitalize()
    return subject


def key_facts(item: ReviewItem, now: datetime) -> str:
    """Who it is from and how old it is, which is what a decision turns on."""
    people = participants(item)
    facts: list[str] = []
    if people:
        first = people[0]
        facts.append(f"{first} +{len(people) - 1} more" if len(people) > 1 else first)
    age = relative_age(item.received_at, now) if item.received_at else ""
    if age:
        facts.append(age)
    return " · ".join(facts)


def participants(item: ReviewItem) -> list[str]:
    return [value for value in (item.participants or []) if isinstance(value, str)]


def decision_text(item: ReviewItem, labels: dict[str, str]) -> str:
    """What has been decided, if anything."""
    state = STATE_LABELS.get(item.state, item.state)
    if item.approved_action:
        taken = labels.get(item.approved_action, item.approved_action)
        return f"{state}: {with_destination(taken, item.approved_action, item.approved_params)}"
    return state


def relative_age(moment: datetime, now: datetime | None = None) -> str:
    reference = now or datetime.now(UTC)
    stamped = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    days = (reference - stamped).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    months = days // 30
    if months < 12:
        return f"{months} month{'s' if months > 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years > 1 else ''} ago"


def offers_anything(offered: ScreenAction, capability: CapabilityConfig) -> bool:
    """Whether this capability can take the offered decision at all."""
    if offered.scope == "group" and not capability.approval.allow_bulk_decisions:
        return False
    if offered.action is None:
        return True
    return offered.action in ACTION_VALUES and capability.permits(ActionKind(offered.action))


def available_actions(
    screen: ScreenConfig,
    capability: CapabilityConfig,
    item: ReviewItem,
) -> list[str]:
    """The offered actions this row will actually accept.

    Asked of the same check the decision endpoint runs, so the contract and the
    enforcement cannot drift apart: a row never advertises a decision that
    would come back refused.
    """
    allowed: list[str] = []
    for offered in screen.actions:
        if offered.scope != "item" or not offers_anything(offered, capability):
            continue
        action = ActionKind(offered.action) if offered.action else None
        try:
            check_decision(capability, item, offered.decision, action, None)
        except DecisionRefused:
            continue
        allowed.append(offered.id)
    return allowed


def render_action(
    offered: ScreenAction,
    run: ReviewRun,
    capability: CapabilityConfig,
) -> RenderedAction:
    body: dict[str, str] = {"decision": offered.decision.value}
    if offered.action is not None:
        body["action"] = offered.action

    if offered.scope == "group":
        path = GROUP_DECISION_PATH.format(run_id=run.id, capability_key=capability.key)
    else:
        path = ITEM_DECISION_PATH.format(run_id=run.id)

    return RenderedAction(
        id=offered.id,
        label=offered.label,
        decision=offered.decision.value,
        action=offered.action,
        scope=offered.scope,
        method="POST",
        path=path,
        body=body,
        params=action_params(offered, capability),
    )


def action_params(offered: ScreenAction, capability: CapabilityConfig) -> list[RenderedParam]:
    """What the reader must add to the body for this action to be taken.

    Only a move needs anything, and what it needs is a folder from this
    capability's own list, so the contract carries the list rather than
    leaving a renderer to guess at folder names.
    """
    if offered.action != ActionKind.GMAIL_MOVE:
        return []
    return [
        RenderedParam(
            name=DESTINATION_PARAM,
            label="Folder",
            required=True,
            choices=list(capability.gmail.destinations),
        )
    ]


def render_footer(screen: ScreenConfig, view: GroupView, run: ReviewRun) -> str:
    if not screen.footer:
        return ""
    total = len(view.items)
    pending = sum(1 for item in view.items if item.state == ItemState.PENDING)
    approved = sum(1 for item in view.items if item.state == ItemState.APPROVED)
    return screen.footer.format_map(
        {
            "capability": view.capability.name,
            "screen_id": screen.id,
            "total": total,
            "pending": pending,
            "decided": total - pending,
            "approved": approved,
            "review_date": run.review_date.isoformat(),
        }
    )
