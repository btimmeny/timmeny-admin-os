import json

from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, AsyncIterator

import httpx

from adminos.logging import get_logger


MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_API_VERSION = "2024-10"
REQUEST_TIMEOUT_SECONDS = 20.0
PAGE_SIZE = 100
EXTERNAL_SYSTEM = "monday"

NAME_COLUMN_ID = "name"
STATUS_COLUMN_ID = "status"
ADMIN_OS_ID_COLUMN_ID = "text_mm5prcay"
ACTION_DATE_COLUMN_ID = "date_mkq4x1z4"
READ_COLUMN_IDS = (STATUS_COLUMN_ID, ADMIN_OS_ID_COLUMN_ID, ACTION_DATE_COLUMN_ID)

DONE_STATUS = "Done"

logger = get_logger(__name__)


class MondayError(RuntimeError):
    """Raised when Monday cannot be reached or returns an unusable response."""


class MondayAuthError(MondayError):
    """Raised when the API token is missing or rejected."""


class ItemFilter(StrEnum):
    """Which items to read from a board."""

    OPEN = "open"
    DONE = "done"
    ALL = "all"


@dataclass(frozen=True)
class MondayItem:
    item_id: str
    name: str
    group: str | None
    status: str | None
    admin_os_id: str | None
    action_date: str | None

    @property
    def is_done(self) -> bool:
        return (self.status or "").casefold() == DONE_STATUS.casefold()


ITEM_FIELDS = f"""
  id
  name
  group {{ title }}
  column_values(ids: {json.dumps(list(READ_COLUMN_IDS))}) {{ id text }}
"""


class MondayClient:
    """A read-only Monday GraphQL client for the coordination layer.

    Separate from the connector in `main.py`, which raises `HTTPException` from
    inside the adapter and is therefore unusable outside a request. Writes are
    deliberately absent until identity and verification exist; see ADR-0002.
    """

    def __init__(self, token: str, http_client: httpx.AsyncClient) -> None:
        self._token = token
        self._http_client = http_client
        self._done_label_indexes: dict[str, int] = {}

    async def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http_client.post(
                MONDAY_API_URL,
                json={"query": query, "variables": variables},
                headers={
                    "Authorization": self._token,
                    "Content-Type": "application/json",
                    "API-Version": MONDAY_API_VERSION,
                },
            )
        except httpx.HTTPError as exc:
            raise MondayError("Could not contact Monday.") from exc

        if response.status_code in {401, 403}:
            raise MondayAuthError("Monday rejected the API token.")
        if response.status_code >= 400:
            raise MondayError(f"Monday returned HTTP {response.status_code}.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise MondayError("Monday returned an invalid JSON response.") from exc
        if not isinstance(payload, dict):
            raise MondayError("Monday returned an unexpected response shape.")

        errors = payload.get("errors")
        if errors:
            # Error text can quote item names, so only the count is logged.
            logger.error("monday graphql request failed with %d error(s)", len(errors))
            raise MondayError("Monday rejected the GraphQL request.")

        data = payload.get("data")
        if not isinstance(data, dict):
            raise MondayError("Monday returned no data.")
        return data

    async def read_board_name(self, board_id: str) -> str | None:
        data = await self.execute(
            "query BoardName($board_id: ID!) { boards(ids: [$board_id]) { name } }",
            {"board_id": board_id},
        )
        boards = data.get("boards")
        if not isinstance(boards, list) or not boards:
            raise MondayError(f"Monday has no board {board_id}.")
        name = boards[0].get("name") if isinstance(boards[0], dict) else None
        return name if isinstance(name, str) else None

    async def read_done_label_index(self, board_id: str) -> int:
        """Return the index Monday filters the Done label by.

        Filter rules on a status column compare against the label's index, not
        its text. Passing the text silently matches nothing, which returns the
        whole board dressed as a filtered result.
        """
        cached = self._done_label_indexes.get(board_id)
        if cached is not None:
            return cached

        data = await self.execute(
            """
            query StatusLabels($board_id: ID!, $column_id: String!) {
              boards(ids: [$board_id]) {
                columns(ids: [$column_id]) { settings_str }
              }
            }
            """,
            {"board_id": board_id, "column_id": STATUS_COLUMN_ID},
        )
        boards = data.get("boards")
        if not isinstance(boards, list) or not boards:
            raise MondayError(f"Monday has no board {board_id}.")
        columns = boards[0].get("columns") if isinstance(boards[0], dict) else None
        if not isinstance(columns, list) or not columns:
            raise MondayError(f"Board {board_id} has no {STATUS_COLUMN_ID!r} column.")

        index = find_label_index(columns[0].get("settings_str"), DONE_STATUS)
        if index is None:
            raise MondayError(
                f"Board {board_id} has no {DONE_STATUS!r} label on its status column."
            )
        self._done_label_indexes[board_id] = index
        return index

    async def list_items(
        self,
        board_id: str,
        item_filter: ItemFilter = ItemFilter.OPEN,
        contains: str | None = None,
        limit: int = 500,
    ) -> list[MondayItem]:
        """Return board items, newest page first, filtered by status and name.

        Both filters are applied by Monday through `query_params` rather than
        locally. This board holds a thousand-plus items of which the vast
        majority are done; filtering after paging would either burn the API's
        complexity budget on every call or silently filter one page and call it
        the answer.
        """
        done_index = (
            None
            if item_filter is ItemFilter.ALL
            else await self.read_done_label_index(board_id)
        )
        query = f"""
        query BoardItems($board_id: ID!, $limit: Int!, $rules: [ItemsQueryRule!]) {{
          boards(ids: [$board_id]) {{
            items_page(limit: $limit, query_params: {{rules: $rules}}) {{
              cursor
              items {{{ITEM_FIELDS}}}
            }}
          }}
        }}
        """
        data = await self.execute(
            query,
            {
                "board_id": board_id,
                "limit": min(limit, PAGE_SIZE),
                "rules": build_rules(item_filter, done_index, contains),
            },
        )

        boards = data.get("boards")
        if not isinstance(boards, list) or not boards:
            raise MondayError(f"Monday has no board {board_id}.")
        page = boards[0].get("items_page") if isinstance(boards[0], dict) else None
        if not isinstance(page, dict):
            raise MondayError("Monday returned no items page.")

        items = build_items(page.get("items"))
        cursor = page.get("cursor")
        while isinstance(cursor, str) and cursor and len(items) < limit:
            page = await self.read_next_page(cursor, min(limit - len(items), PAGE_SIZE))
            items.extend(build_items(page.get("items")))
            cursor = page.get("cursor")

        return items[:limit]

    async def read_next_page(self, cursor: str, limit: int) -> dict[str, Any]:
        query = f"""
        query NextItems($cursor: String!, $limit: Int!) {{
          next_items_page(cursor: $cursor, limit: $limit) {{
            cursor
            items {{{ITEM_FIELDS}}}
          }}
        }}
        """
        data = await self.execute(query, {"cursor": cursor, "limit": limit})
        page = data.get("next_items_page")
        if not isinstance(page, dict):
            raise MondayError("Monday returned no next items page.")
        return page


@asynccontextmanager
async def open_monday_client(token: str) -> AsyncIterator[MondayClient]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http_client:
        yield MondayClient(token, http_client)


def find_label_index(settings: Any, label: str) -> int | None:
    """Return the index of a status label from the column's settings JSON."""
    if not isinstance(settings, str):
        return None
    try:
        parsed = json.loads(settings)
    except ValueError:
        return None
    labels = parsed.get("labels") if isinstance(parsed, dict) else None
    if not isinstance(labels, dict):
        return None

    wanted = label.casefold()
    for index, text in labels.items():
        if isinstance(text, str) and text.casefold() == wanted:
            try:
                return int(index)
            except ValueError:
                return None
    return None


def build_rules(
    item_filter: ItemFilter,
    done_index: int | None = None,
    contains: str | None = None,
) -> list[dict[str, Any]] | None:
    """Translate a filter into Monday query rules.

    An item with no status at all counts as open: it has not been marked done,
    and `not_any_of` keeps it, which is the behaviour the board owner expects
    from "what is outstanding".
    """
    rules: list[dict[str, Any]] = []
    if item_filter is not ItemFilter.ALL:
        if done_index is None:
            raise MondayError(f"Filtering by {item_filter} needs the Done label index.")
        rules.append(
            {
                "column_id": STATUS_COLUMN_ID,
                "compare_value": [done_index],
                "operator": "not_any_of" if item_filter is ItemFilter.OPEN else "any_of",
            }
        )
    if contains:
        rules.append(
            {
                "column_id": NAME_COLUMN_ID,
                "compare_value": [contains],
                "operator": "contains_text",
            }
        )
    return rules or None


def build_items(raw_items: Any) -> list[MondayItem]:
    if not isinstance(raw_items, list):
        return []
    return [build_item(raw) for raw in raw_items if isinstance(raw, dict)]


def build_item(raw: dict[str, Any]) -> MondayItem:
    columns = {
        column.get("id"): column.get("text")
        for column in raw.get("column_values") or []
        if isinstance(column, dict)
    }
    group = raw.get("group")
    return MondayItem(
        item_id=str(raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        group=group.get("title") if isinstance(group, dict) else None,
        status=read_text(columns.get(STATUS_COLUMN_ID)),
        admin_os_id=read_text(columns.get(ADMIN_OS_ID_COLUMN_ID)),
        action_date=read_text(columns.get(ACTION_DATE_COLUMN_ID)),
    )


def read_text(value: Any) -> str | None:
    """Monday returns an empty string for an unset text column, not null."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
