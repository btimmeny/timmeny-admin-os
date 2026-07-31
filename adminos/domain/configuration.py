"""The configuration Brian writes in Monday, read as Admin OS finds it.

A board of processes and email rules is configuration in the sense that
matters: he edits it without a deploy, and what it says is what the review is
supposed to do. So this reads it and reports it, and does nothing else with
it — no caching past the call, no persistence, no review state.

Every part is checked against the board before anything is read from it. The
board id is an environment variable and the columns are found by their titles,
which means a renamed column is a real possibility; a read that shrugged at
one would hand back configuration with the instructions missing, and a rule
with no instructions reads exactly like a rule that says nothing. So a missing
column, a missing label or a status filter that did not apply is a refusal
naming what the board has instead, never a shorter answer.
"""

import re

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from adminos.adapters.monday import MondayClient, MondayColumn, MondayItem
from adminos.logging import get_logger


logger = get_logger(__name__)

ITEM_LIMIT = 200

SOURCE = "monday"

ACTIVE_LABEL = "Active"
"""The only status whose items are configuration. Draft and Inactive are not."""

PROCESS_TYPE = "Process"
EMAIL_RULE_TYPE = "Email Rule"

STATUS_TITLE = "Status"
TYPE_TITLE = "Configuration Type"
TRIGGER_TITLE = "Trigger / Match"
INSTRUCTIONS_TITLE = "Instructions / Logic"
CONTEXT_TITLE = "Context Needed"
EXPECTED_OUTPUT_TITLE = "Expected Output"
ORDER_TITLE = "Order"
GUARDRAILS_TITLE = "Notes / Guardrails"

REQUIRED_TITLES = (
    STATUS_TITLE,
    TYPE_TITLE,
    TRIGGER_TITLE,
    INSTRUCTIONS_TITLE,
    CONTEXT_TITLE,
    EXPECTED_OUTPUT_TITLE,
    ORDER_TITLE,
    GUARDRAILS_TITLE,
)
"""The columns a configuration item is made of, by the titles on the board.

Titles rather than ids because the board itself is configuration: the id of a
column Monday generated on a board named by an environment variable is not
something this repository can know. The cost is that renaming a column breaks
the read, which is why it breaks loudly.
"""

EMAIL_CONFIGURATION = "email"
"""The only configuration a caller may ask for while only email is built."""


class ConfigurationUnavailable(RuntimeError):
    """Raised when the configuration board cannot be read as configuration.

    Not the same as a board with nothing active on it. This is a board that is
    not there, a column that is not on it, or a filter that did not apply —
    each of which would otherwise come back as an empty or partial answer that
    reads like a complete one.
    """


@dataclass(frozen=True)
class ConfigurationEntry:
    """One active item, as the board holds it."""

    item_id: str
    key: str
    name: str
    group_name: str | None
    configuration_type: str
    trigger: str
    instructions: str
    context_needed: str
    expected_output: str
    order: int | None
    guardrails: str

    def payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "key": self.key,
            "name": self.name,
            "group_name": self.group_name,
            "configuration_type": self.configuration_type,
            "trigger": self.trigger,
            "instructions": self.instructions,
            "context_needed": self.context_needed,
            "expected_output": self.expected_output,
            "order": self.order,
            "guardrails": self.guardrails,
        }


@dataclass(frozen=True)
class Configuration:
    """What the board said, and when it said it."""

    board_id: str
    board_name: str
    configuration_type: str
    retrieved_at: datetime
    processes: tuple[ConfigurationEntry, ...]
    email_configurations: tuple[ConfigurationEntry, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "source": SOURCE,
            "board_id": self.board_id,
            "board_name": self.board_name,
            "configuration_type": self.configuration_type,
            "retrieved_at": self.retrieved_at.isoformat(),
            "processes": [entry.payload() for entry in self.processes],
            "email_configurations": [
                entry.payload() for entry in self.email_configurations
            ],
        }


@dataclass(frozen=True)
class ResolvedColumns:
    """The configuration columns, found on the board by title."""

    by_title: Mapping[str, MondayColumn]
    active_index: int

    def column_id(self, title: str) -> str:
        return self.by_title[title].column_id

    def ids(self) -> list[str]:
        return [column.column_id for column in self.by_title.values()]


async def read_configuration(
    client: MondayClient,
    board_id: str,
    configuration_type: str = EMAIL_CONFIGURATION,
    limit: int = ITEM_LIMIT,
) -> Configuration:
    """The active processes and email rules on the configuration board.

    Read every time. The board is where Brian changes his mind, and a cached
    answer is a review run against what he used to think.
    """
    if configuration_type != EMAIL_CONFIGURATION:
        raise ConfigurationUnavailable(
            f"Only {EMAIL_CONFIGURATION!r} configuration is built. To-do rules and "
            "reference data are on the board and are not read here yet, and "
            "answering with the email ones under another name would be a lie "
            "about what was applied."
        )

    board_name, columns = await client.read_board(board_id)
    resolved = resolve_columns(board_id, board_name, columns)
    items = await client.list_items_matching(
        board_id=board_id,
        rules=[
            {
                "column_id": resolved.column_id(STATUS_TITLE),
                "compare_value": [resolved.active_index],
                "operator": "any_of",
            }
        ],
        column_ids=resolved.ids(),
        operator="and",
        limit=limit,
    )
    active = [item for item in items if is_active(item, resolved)]
    if len(active) != len(items):
        raise ConfigurationUnavailable(
            f"Monday returned {len(items) - len(active)} of {len(items)} items whose "
            f"{STATUS_TITLE} is not {ACTIVE_LABEL!r}, so the filter did not apply. "
            "Nothing was read: a board that came back unfiltered would put draft "
            "and retired configuration into a review as though Brian had agreed "
            "to it."
        )

    entries = [build_entry(item, resolved) for item in active]
    configuration = Configuration(
        board_id=board_id,
        board_name=board_name,
        configuration_type=configuration_type,
        retrieved_at=datetime.now(UTC),
        processes=in_order(entries, PROCESS_TYPE),
        email_configurations=in_order(entries, EMAIL_RULE_TYPE),
    )
    logger.info(
        "read %d process(es) and %d email rule(s) from board %s",
        len(configuration.processes),
        len(configuration.email_configurations),
        board_id,
    )
    return configuration


def resolve_columns(
    board_id: str, board_name: str, columns: list[MondayColumn]
) -> ResolvedColumns:
    """Find every configuration column on the board, or refuse to read it."""
    by_title: dict[str, MondayColumn] = {}
    available = [column.title for column in columns]
    for title in REQUIRED_TITLES:
        found = next((column for column in columns if column.title == title), None)
        if found is None:
            raise ConfigurationUnavailable(
                f"Board {board_name} ({board_id}) has no {title!r} column, so its "
                f"items are not configuration this can read. It has: "
                f"{', '.join(repr(text) for text in available) or 'no columns'}."
            )
        by_title[title] = found

    status = by_title[STATUS_TITLE]
    active_index = status.index_of(ACTIVE_LABEL)
    if active_index is None:
        raise ConfigurationUnavailable(
            f"The {STATUS_TITLE!r} column on board {board_name} ({board_id}) has no "
            f"{ACTIVE_LABEL!r} label, so nothing on it can be known to be active. "
            f"Its labels are: {labels_of(status)}."
        )

    kinds = by_title[TYPE_TITLE]
    offered = kinds.labels()
    missing = [label for label in (PROCESS_TYPE, EMAIL_RULE_TYPE) if label not in offered]
    if missing:
        raise ConfigurationUnavailable(
            f"The {TYPE_TITLE!r} column on board {board_name} ({board_id}) offers no "
            f"{', '.join(repr(label) for label in missing)}, so an email review would "
            f"read an empty list as though nothing were configured. Its labels are: "
            f"{labels_of(kinds)}."
        )

    return ResolvedColumns(by_title=by_title, active_index=active_index)


def is_active(item: MondayItem, resolved: ResolvedColumns) -> bool:
    return text_of(item, resolved, STATUS_TITLE) == ACTIVE_LABEL


def build_entry(item: MondayItem, resolved: ResolvedColumns) -> ConfigurationEntry:
    return ConfigurationEntry(
        item_id=item.item_id,
        key=slug(item.name),
        name=item.name,
        group_name=item.group,
        configuration_type=text_of(item, resolved, TYPE_TITLE),
        trigger=text_of(item, resolved, TRIGGER_TITLE),
        instructions=text_of(item, resolved, INSTRUCTIONS_TITLE),
        context_needed=text_of(item, resolved, CONTEXT_TITLE),
        expected_output=text_of(item, resolved, EXPECTED_OUTPUT_TITLE),
        order=whole_number(text_of(item, resolved, ORDER_TITLE)),
        guardrails=text_of(item, resolved, GUARDRAILS_TITLE),
    )


def in_order(
    entries: list[ConfigurationEntry], configuration_type: str
) -> tuple[ConfigurationEntry, ...]:
    """The entries of one type, by Order, with an unordered one last.

    An item with no Order sorts last rather than first: a rule Brian never
    placed should not silently become the first thing applied.
    """
    wanted = [entry for entry in entries if entry.configuration_type == configuration_type]
    return tuple(
        sorted(
            wanted,
            key=lambda entry: (entry.order is None, entry.order or 0, entry.name),
        )
    )


def text_of(item: MondayItem, resolved: ResolvedColumns, title: str) -> str:
    return (item.values.get(resolved.column_id(title)) or "").strip()


def whole_number(text: str) -> int | None:
    try:
        return int(float(text))
    except ValueError:
        return None


def labels_of(column: MondayColumn) -> str:
    return ", ".join(repr(text) for text in sorted(column.labels())) or "none"


def slug(name: str) -> str:
    """A readable key for a name. The stable reference is the Monday item id."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.casefold())).strip("_")


__all__ = [
    "Configuration",
    "ConfigurationEntry",
    "ConfigurationUnavailable",
    "EMAIL_CONFIGURATION",
    "read_configuration",
]
