"""A Monday scope is exact, or the review does not look.

The failure this guards against is quiet. A column that has been renamed, a
label that was never created, an id copied from the wrong board: Monday
answers all of them without complaint, matches nothing, and hands back a
thousand-item board that looks exactly like a filtered result. These tests
hold the scope to being checked against the board before it is queried, to
naming what is missing when it is, and to never widening.
"""

import asyncio
import json

from typing import Any

import httpx
import pytest

from adminos.adapters.monday import MondayClient, MondayColumn
from adminos.domain.boards import (
    BoardScopeNotConfigured,
    BoardScopeUnresolved,
    read_scoped_items,
    resolve_board_scope,
)
from adminos.domain.playbook import ColumnFilterConfig, MondayScopeConfig


BOARD_ID = "8962223984"
BOARD_NAME = "To Do List"
TODAY_COLUMN = "status"
CADENCE_COLUMN = "color_mkq6wnv7"

TODAY_SETTINGS = json.dumps(
    {"labels": {"0": "In Progress", "2": "Working on it today", "3": "Done"}}
)
CADENCE_SETTINGS = json.dumps({"labels": {"0": "Weekly", "5": "Daily"}})


def board_shape(columns: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"boards": [{"name": BOARD_NAME, "columns": columns}]}}


def column(
    column_id: str, title: str, settings: str | None, kind: str = "status"
) -> dict[str, Any]:
    return {"id": column_id, "title": title, "type": kind, "settings_str": settings}


BOARD_COLUMNS = [
    column(TODAY_COLUMN, "Status", TODAY_SETTINGS),
    column(CADENCE_COLUMN, "Cadence", CADENCE_SETTINGS),
    column("name", "Name", None, kind="name"),
]


def item(item_id: str, name: str, today: str | None, cadence: str | None) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "group": {"title": "Tasks | Action Items"},
        "column_values": [
            {"id": TODAY_COLUMN, "text": today},
            {"id": CADENCE_COLUMN, "text": cadence},
        ],
    }


def items_page(raw: list[dict[str, Any]], cursor: str | None = None) -> dict[str, Any]:
    return {"data": {"boards": [{"items_page": {"cursor": cursor, "items": raw}}]}}


def client_returning(
    responses: list[dict[str, Any]], requests: list[dict[str, Any]] | None = None
) -> httpx.AsyncClient:
    remaining = iter(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(json.loads(request.read()))
        return httpx.Response(200, json=next(remaining))

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def scope_config(
    today_labels: list[str] | None = None,
    cadence_labels: list[str] | None = None,
    today_column: str = TODAY_COLUMN,
    cadence_column: str = CADENCE_COLUMN,
) -> MondayScopeConfig:
    return MondayScopeConfig(
        board_id=BOARD_ID,
        filters=[
            ColumnFilterConfig(
                column_id=today_column, labels=today_labels or ["Working on it today"]
            ),
            ColumnFilterConfig(column_id=cadence_column, labels=cadence_labels or ["Daily"]),
        ],
    )


UNSET = MondayScopeConfig(
    board_id="0", filters=[ColumnFilterConfig(column_id="unset", labels=["unset"])]
)
"""A stand-in for "the caller did not say", so `None` can mean unconfigured."""


def resolve(
    responses: list[dict[str, Any]],
    config: MondayScopeConfig | None = UNSET,
    requests: list[dict[str, Any]] | None = None,
) -> Any:
    async def run() -> Any:
        async with client_returning(responses, requests) as http_client:
            return await resolve_board_scope(
                MondayClient("token", http_client),
                scope_config() if config is UNSET else config,
            )

    return asyncio.run(run())


def read(
    responses: list[dict[str, Any]],
    requests: list[dict[str, Any]] | None = None,
    config: MondayScopeConfig | None = None,
) -> Any:
    async def run() -> Any:
        async with client_returning(responses, requests) as http_client:
            client = MondayClient("token", http_client)
            scope = await resolve_board_scope(client, config or scope_config())
            return await read_scoped_items(client, scope)

    return asyncio.run(run())


def test_a_scope_resolves_every_label_to_the_index_monday_filters_by() -> None:
    """Rules compare indexes; sending the text matches nothing and looks fine."""
    scope = resolve([board_shape(BOARD_COLUMNS)])

    assert scope.board_name == BOARD_NAME
    assert [filter.column_id for filter in scope.filters] == [TODAY_COLUMN, CADENCE_COLUMN]
    assert [filter.indexes for filter in scope.filters] == [(2,), (5,)]
    assert scope.rules() == [
        {"column_id": TODAY_COLUMN, "compare_value": [2], "operator": "any_of"},
        {"column_id": CADENCE_COLUMN, "compare_value": [5], "operator": "any_of"},
    ]


def test_a_scope_says_exactly_which_items_it_is_of() -> None:
    scope = resolve([board_shape(BOARD_COLUMNS)])

    assert scope.describes() == (
        "Items on To Do List where Status is 'Working on it today' or "
        "Cadence is 'Daily'."
    )


def test_no_configured_board_is_no_monday_review_rather_than_a_guess() -> None:
    with pytest.raises(BoardScopeNotConfigured):
        resolve([board_shape(BOARD_COLUMNS)], config=None)


def test_a_column_the_board_does_not_have_stops_the_review() -> None:
    """Naming what the board does have is the difference between a bug and a fix."""
    with pytest.raises(BoardScopeUnresolved) as raised:
        resolve([board_shape(BOARD_COLUMNS)], scope_config(cadence_column="color_missing"))

    assert "no column 'color_missing'" in str(raised.value)
    assert CADENCE_COLUMN in str(raised.value)


def test_a_label_the_column_does_not_have_stops_the_review() -> None:
    with pytest.raises(BoardScopeUnresolved) as raised:
        resolve([board_shape(BOARD_COLUMNS)], scope_config(cadence_labels=["Every day"]))

    assert "no label 'Every day'" in str(raised.value)
    assert "'Daily'" in str(raised.value)


def test_a_label_that_is_nearly_right_is_not_right() -> None:
    """'daily' and 'Daily' are different labels to whoever owns the board."""
    with pytest.raises(BoardScopeUnresolved):
        resolve([board_shape(BOARD_COLUMNS)], scope_config(cadence_labels=["daily"]))


def test_a_scoped_read_asks_monday_to_apply_the_filter() -> None:
    """Filtering after paging is how an API budget is spent proving a point."""
    requests: list[dict[str, Any]] = []
    items = read(
        [
            board_shape(BOARD_COLUMNS),
            items_page(
                [
                    item("1", "Renew domain", "Working on it today", None),
                    item("2", "Morning review", None, "Daily"),
                ]
            ),
        ],
        requests,
    )

    assert [found.item_id for found in items] == ["1", "2"]
    variables = requests[1]["variables"]
    assert variables["operator"] == "or"
    assert variables["rules"] == [
        {"column_id": TODAY_COLUMN, "compare_value": [2], "operator": "any_of"},
        {"column_id": CADENCE_COLUMN, "compare_value": [5], "operator": "any_of"},
    ]


def test_an_item_matching_neither_filter_means_the_filter_did_not_apply() -> None:
    """An unfiltered board arriving as today's work is refused, not trimmed.

    Trimming would be worse than failing: the review would look right, on a
    page of a board nobody scoped, and the items past the first page would be
    silently absent.
    """
    with pytest.raises(BoardScopeUnresolved) as raised:
        read(
            [
                board_shape(BOARD_COLUMNS),
                items_page(
                    [
                        item("1", "Renew domain", "Working on it today", None),
                        item("2", "Something else entirely", "Done", None),
                    ]
                ),
            ]
        )

    assert "did not apply" in str(raised.value)


def test_a_dropdown_column_resolves_its_labels_too() -> None:
    """Status and dropdown columns describe their labels differently."""
    dropdown = json.dumps({"labels": [{"id": 3, "name": "Daily"}, {"id": 4, "name": "Weekly"}]})
    scope = resolve(
        [
            board_shape(
                [
                    column(TODAY_COLUMN, "Status", TODAY_SETTINGS),
                    column(CADENCE_COLUMN, "Cadence", dropdown, kind="dropdown"),
                ]
            )
        ]
    )

    assert scope.filters[1].indexes == (3,)


def test_a_column_with_no_settings_has_no_labels_to_match() -> None:
    assert MondayColumn("text_1", "Notes", "text", None).labels() == ()
    assert MondayColumn("text_1", "Notes", "text", "not json").index_of("Daily") is None
