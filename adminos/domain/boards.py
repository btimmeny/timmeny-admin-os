"""Which Monday items count as today's work, and the exactness required first.

A Gmail review is scoped by a label Gmail either has or does not. A Monday
review is scoped by a board id, a column id and a label text, and every one of
those can be right in the configuration and absent from the board — renamed,
moved, or never created. Monday does not complain about that. A rule naming a
column that is not there, or a label index that does not exist, matches
nothing, and a filter that matches nothing on a thousand-item board hands back
the whole board looking exactly like an answer.

So the board is read before it is queried, every configured id and label is
checked against what is actually on it, and anything missing stops the review
with what was looked for and what the board has instead. The one thing that
never happens is widening: a scope that cannot be resolved is reported, not
replaced with a bigger one.
"""

from dataclasses import dataclass
from typing import Any, Sequence

from adminos.adapters.monday import (
    MondayClient,
    MondayColumn,
    MondayItem,
)
from adminos.domain.playbook import ColumnFilterConfig, MondayScopeConfig
from adminos.logging import get_logger


logger = get_logger(__name__)

DEFAULT_ITEM_LIMIT = 200

ANY_OF_THE_FILTERS = "or"
"""How Monday spells "qualifying on either column"."""


class BoardScopeNotConfigured(RuntimeError):
    """Raised when the playbook names no Monday board to review."""


class BoardScopeUnresolved(RuntimeError):
    """Raised when the configured board, column or label is not on the board.

    Separate from a Monday outage: the board answered, and what it said is
    that this scope does not describe it. Nothing about that is fixed by
    retrying, and nothing about it justifies reading more of the board.
    """


@dataclass(frozen=True)
class ResolvedFilter:
    """A configured filter, confirmed against the column it names."""

    column_id: str
    column_title: str
    labels: tuple[str, ...]
    indexes: tuple[int, ...]

    def rule(self) -> dict[str, Any]:
        """The Monday rule for this filter, comparing indexes rather than text."""
        return {
            "column_id": self.column_id,
            "compare_value": list(self.indexes),
            "operator": "any_of",
        }

    def matches(self, item: MondayItem) -> bool:
        return item.values.get(self.column_id) in self.labels


@dataclass(frozen=True)
class ResolvedBoardScope:
    """A board scope every part of which was found on the board itself."""

    board_id: str
    board_name: str
    filters: tuple[ResolvedFilter, ...]

    def rules(self) -> list[dict[str, Any]]:
        return [filter.rule() for filter in self.filters]

    def column_ids(self) -> list[str]:
        return [filter.column_id for filter in self.filters]

    def matches(self, item: MondayItem) -> bool:
        return any(filter.matches(item) for filter in self.filters)

    def describes(self) -> str:
        """One sentence saying exactly which items this scope is of."""
        clauses = " or ".join(
            f"{filter.column_title} is "
            + " or ".join(repr(label) for label in filter.labels)
            for filter in self.filters
        )
        return f"Items on {self.board_name} where {clauses}."


async def resolve_board_scope(
    client: MondayClient, config: MondayScopeConfig | None
) -> ResolvedBoardScope:
    """Confirm the configured scope against the board, or refuse to look."""
    if config is None:
        raise BoardScopeNotConfigured(
            "The playbook names no Monday board, so there is no Monday work to "
            "review. Name the board, the column and the labels that qualify an "
            "item, and this reads exactly those."
        )

    board_name, columns = await client.read_board(config.board_id)
    by_id = {column.column_id: column for column in columns}
    resolved = tuple(
        resolve_filter(config.board_id, board_name, by_id, columns, wanted)
        for wanted in config.filters
    )
    scope = ResolvedBoardScope(
        board_id=config.board_id, board_name=board_name, filters=resolved
    )
    logger.info(
        "monday scope resolved on board %s with %d filter(s)",
        config.board_id,
        len(resolved),
    )
    return scope


def resolve_filter(
    board_id: str,
    board_name: str,
    by_id: dict[str, MondayColumn],
    columns: Sequence[MondayColumn],
    wanted: ColumnFilterConfig,
) -> ResolvedFilter:
    """One configured filter, or a refusal naming what the board has instead."""
    column = by_id.get(wanted.column_id)
    if column is None:
        raise BoardScopeUnresolved(
            f"Board {board_name} ({board_id}) has no column {wanted.column_id!r}. "
            f"It has: {', '.join(sorted(by_id))}."
        )

    available = column.labels()
    indexes: list[int] = []
    for label in wanted.labels:
        index = column.index_of(label)
        if index is None:
            raise BoardScopeUnresolved(
                f"Column {column.title!r} ({column.column_id}) on board "
                f"{board_name} has no label {label!r}. Its labels are: "
                f"{', '.join(repr(text) for text in sorted(available)) or 'none'}."
            )
        indexes.append(index)

    return ResolvedFilter(
        column_id=column.column_id,
        column_title=column.title,
        labels=tuple(wanted.labels),
        indexes=tuple(indexes),
    )


async def read_scoped_items(
    client: MondayClient,
    scope: ResolvedBoardScope,
    limit: int = DEFAULT_ITEM_LIMIT,
) -> list[MondayItem]:
    """The items the scope describes, checked to be the items that came back.

    Monday applies the rules, and then every item is checked again here
    against the labels that were asked for. Both are needed: the filter is
    what keeps a thousand-item board off the wire, and the check is what
    notices when it did not apply. An unfiltered board arriving under the name
    of today's work is the failure this whole module exists to prevent, so it
    is refused rather than trimmed.
    """
    items = await client.list_items_matching(
        board_id=scope.board_id,
        rules=scope.rules(),
        column_ids=scope.column_ids(),
        operator=ANY_OF_THE_FILTERS,
        limit=limit,
    )
    outside = [item for item in items if not scope.matches(item)]
    if outside:
        logger.error(
            "monday returned %d item(s) outside the scope on board %s",
            len(outside),
            scope.board_id,
        )
        raise BoardScopeUnresolved(
            f"Monday returned {len(outside)} of {len(items)} items that do not "
            f"match the scope, so the filter did not apply. {scope.describes()} "
            "Nothing was reviewed: a board that came back unfiltered is not "
            "today's work."
        )
    return items
