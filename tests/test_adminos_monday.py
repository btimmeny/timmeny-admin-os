import asyncio
import json

from typing import Any

import httpx
import pytest

from adminos.adapters.monday import (
    ADMIN_OS_ID_COLUMN_ID,
    MONDAY_API_URL,
    STATUS_COLUMN_ID,
    ItemFilter,
    MondayAuthError,
    MondayClient,
    MondayError,
    build_item,
    build_rules,
    find_label_index,
)


BOARD_ID = "8962223984"
DONE_INDEX = 3
# The live board's Status column: label indexes are not their display order.
STATUS_SETTINGS = json.dumps(
    {"labels": {"0": "In Progress", "2": "Not Yet Started", "3": "Done"}}
)
STATUS_LABELS_RESPONSE = {
    "data": {"boards": [{"columns": [{"settings_str": STATUS_SETTINGS}]}]}
}


def raw_item(
    item_id: str,
    name: str,
    status: str | None = None,
    admin_os_id: str = "",
    group: str = "Tasks | Action Items",
) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "group": {"title": group},
        "column_values": [
            {"id": STATUS_COLUMN_ID, "text": status},
            {"id": ADMIN_OS_ID_COLUMN_ID, "text": admin_os_id},
        ],
    }


def board_page(items: list[dict[str, Any]], cursor: str | None = None) -> dict[str, Any]:
    return {
        "data": {"boards": [{"items_page": {"cursor": cursor, "items": items}}]}
    }


def next_page(items: list[dict[str, Any]], cursor: str | None = None) -> dict[str, Any]:
    return {"data": {"next_items_page": {"cursor": cursor, "items": items}}}


def client_returning(
    responses: list[dict[str, Any]],
    requests: list[dict[str, Any]] | None = None,
    status_code: int = 200,
) -> httpx.AsyncClient:
    remaining = iter(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(json.loads(request.read()))
        return httpx.Response(status_code, json=next(remaining))

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def read_items(
    responses: list[dict[str, Any]],
    requests: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> Any:
    """Read the board, prefixing the status-label lookup a filtered read makes."""
    filtered = kwargs.get("item_filter", ItemFilter.OPEN) is not ItemFilter.ALL
    prefix = [STATUS_LABELS_RESPONSE] if filtered else []

    async def run() -> Any:
        async with client_returning(prefix + responses, requests) as http_client:
            return await MondayClient("token", http_client).list_items(BOARD_ID, **kwargs)

    return asyncio.run(run())


def test_open_filter_excludes_done_items_at_the_source() -> None:
    rules = build_rules(ItemFilter.OPEN, DONE_INDEX)

    assert rules == [
        {"column_id": "status", "compare_value": [DONE_INDEX], "operator": "not_any_of"}
    ]


def test_done_filter_selects_only_done_items() -> None:
    rules = build_rules(ItemFilter.DONE, DONE_INDEX)

    assert rules is not None
    assert rules[0]["operator"] == "any_of"


def test_all_filter_sends_no_rules() -> None:
    assert build_rules(ItemFilter.ALL) is None


def test_a_status_filter_without_a_label_index_is_refused() -> None:
    """Monday matches nothing on an unknown value, returning the whole board."""
    with pytest.raises(MondayError):
        build_rules(ItemFilter.OPEN)


def test_name_filter_is_added_alongside_the_status_filter() -> None:
    rules = build_rules(ItemFilter.OPEN, DONE_INDEX, "KPMG")

    assert rules is not None
    assert len(rules) == 2
    assert rules[1] == {
        "column_id": "name",
        "compare_value": ["KPMG"],
        "operator": "contains_text",
    }


def test_name_filter_alone_is_sent_for_the_all_filter() -> None:
    rules = build_rules(ItemFilter.ALL, None, "KPMG")

    assert rules is not None
    assert len(rules) == 1
    assert rules[0]["column_id"] == "name"


def test_the_done_label_index_is_read_from_the_board() -> None:
    """Status rules compare against a label index, and 'Done' is not index 0."""
    assert find_label_index(STATUS_SETTINGS, "Done") == DONE_INDEX


def test_a_label_is_matched_regardless_of_case() -> None:
    assert find_label_index(STATUS_SETTINGS, "done") == DONE_INDEX


def test_an_absent_label_has_no_index() -> None:
    assert find_label_index(STATUS_SETTINGS, "Complete") is None


def test_unparseable_column_settings_yield_no_index() -> None:
    assert find_label_index("not json", "Done") is None


def test_a_filtered_read_sends_the_label_index_monday_expects() -> None:
    requests: list[dict[str, Any]] = []

    read_items([board_page([])], requests, item_filter=ItemFilter.DONE)

    assert requests[1]["variables"]["rules"] == [
        {"column_id": "status", "compare_value": [DONE_INDEX], "operator": "any_of"}
    ]


def test_a_board_without_a_done_label_fails_rather_than_reading_everything() -> None:
    settings = json.dumps({"labels": {"0": "Working on it"}})
    response = {"data": {"boards": [{"columns": [{"settings_str": settings}]}]}}

    async def run() -> None:
        async with client_returning([response]) as http_client:
            await MondayClient("token", http_client).list_items(BOARD_ID)

    with pytest.raises(MondayError):
        asyncio.run(run())


def test_the_label_index_is_read_once_per_board() -> None:
    requests: list[dict[str, Any]] = []
    responses = [STATUS_LABELS_RESPONSE, board_page([]), board_page([])]

    async def run() -> None:
        async with client_returning(responses, requests) as http_client:
            client = MondayClient("token", http_client)
            await client.list_items(BOARD_ID)
            await client.list_items(BOARD_ID)

    asyncio.run(run())

    assert sum("StatusLabels" in request["query"] for request in requests) == 1


def test_list_items_reads_the_requested_board() -> None:
    requests: list[dict[str, Any]] = []

    read_items([board_page([raw_item("1", "Annual Taxes | KPMG")])], requests)

    assert all(request["variables"]["board_id"] == BOARD_ID for request in requests)


def test_list_items_maps_columns_onto_the_item() -> None:
    page = board_page(
        [raw_item("1", "Annual Taxes | KPMG", status="In Progress", admin_os_id="ao-1")]
    )

    items = read_items([page], item_filter=ItemFilter.ALL)

    assert items[0].item_id == "1"
    assert items[0].name == "Annual Taxes | KPMG"
    assert items[0].status == "In Progress"
    assert items[0].admin_os_id == "ao-1"
    assert items[0].group == "Tasks | Action Items"
    assert items[0].is_done is False


def test_an_unset_text_column_reads_as_none_not_an_empty_string() -> None:
    """Monday returns "" for an unset text column, which must not look like an id."""
    item = build_item(raw_item("1", "Task", admin_os_id="   "))

    assert item.admin_os_id is None


def test_a_done_item_is_recognised() -> None:
    assert build_item(raw_item("1", "Task", status="Done")).is_done is True


def test_list_items_follows_the_cursor_until_the_limit_is_reached() -> None:
    items = read_items(
        [
            board_page([raw_item("1", "One")], cursor="c1"),
            next_page([raw_item("2", "Two")], cursor="c2"),
            next_page([raw_item("3", "Three")]),
        ],
        item_filter=ItemFilter.ALL,
        limit=10,
    )

    assert [item.item_id for item in items] == ["1", "2", "3"]


def test_list_items_stops_at_the_limit() -> None:
    items = read_items(
        [board_page([raw_item("1", "One"), raw_item("2", "Two")], cursor="c1")],
        item_filter=ItemFilter.ALL,
        limit=2,
    )

    assert len(items) == 2


def test_a_rejected_token_is_distinguishable_from_other_failures() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(401, json={}))
        async with httpx.AsyncClient(transport=transport) as http_client:
            await MondayClient("token", http_client).list_items(BOARD_ID)

    with pytest.raises(MondayAuthError):
        asyncio.run(run())


def test_a_graphql_error_is_surfaced_rather_than_returned_as_no_items() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"errors": [{"message": "bad"}]})
        )
        async with httpx.AsyncClient(transport=transport) as http_client:
            await MondayClient("token", http_client).list_items(BOARD_ID)

    with pytest.raises(MondayError):
        asyncio.run(run())


def test_a_missing_board_is_an_error_not_an_empty_board() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"data": {"boards": []}})
        )
        async with httpx.AsyncClient(transport=transport) as http_client:
            await MondayClient("token", http_client).list_items(BOARD_ID)

    with pytest.raises(MondayError):
        asyncio.run(run())


def test_a_transport_failure_is_wrapped() -> None:
    async def run() -> None:
        def handle(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
            await MondayClient("token", http_client).list_items(BOARD_ID)

    with pytest.raises(MondayError):
        asyncio.run(run())


def test_the_client_sends_no_mutation() -> None:
    """The coordination-layer client is read-only until identity exists."""
    requests: list[dict[str, Any]] = []

    read_items([board_page([raw_item("1", "One")])], requests, item_filter=ItemFilter.OPEN)

    assert all("mutation" not in request["query"] for request in requests)


def test_requests_go_to_the_monday_api() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=board_page([]))

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
            await MondayClient("token", http_client).list_items(
                BOARD_ID, item_filter=ItemFilter.ALL
            )

    asyncio.run(run())

    assert seen == [MONDAY_API_URL]
