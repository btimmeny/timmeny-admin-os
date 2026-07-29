import asyncio

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import adminos.adapters.gmail

from adminos.adapters.gmail import (
    INBOX_LABEL_ID,
    TRASH_LABEL_ID,
    GmailAuthError,
    GmailClient,
    GmailError,
    build_thread,
    collect_participants,
    read_internal_date,
)
from adminos.config import GmailCredentials


CREDENTIALS = GmailCredentials(
    client_id="client-id",
    client_secret="client-secret",
    refresh_token="refresh-token",
)
TOKEN_RESPONSE = {"access_token": "access-1", "expires_in": 3600}


def message(
    message_id: str,
    subject: str,
    sender: str,
    recipients: str,
    internal_date: str,
    snippet: str = "",
) -> dict[str, object]:
    return {
        "id": message_id,
        "internalDate": internal_date,
        "snippet": snippet,
        "labelIds": ["Label_1", "INBOX"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": recipients},
            ]
        },
    }


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient, recording every call."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []

    async def post(
        self,
        url: str,
        data: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self.requests.append(("POST", url, data if data is not None else json))
        return self.respond(url)

    async def get(
        self,
        url: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self.requests.append(("GET", url, params))
        return self.respond(url)

    def respond(self, url: str) -> httpx.Response:
        for pattern, payload in self.routes.items():
            if pattern in url:
                if isinstance(payload, int):
                    return httpx.Response(payload, json={"error": "nope"})
                if callable(payload):
                    return httpx.Response(200, json=payload())
                return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected request to {url}")


def build_client(routes: dict[str, object]) -> tuple[GmailClient, FakeAsyncClient]:
    http_client = FakeAsyncClient({"oauth2.googleapis.com/token": TOKEN_RESPONSE, **routes})
    return GmailClient(CREDENTIALS, http_client), http_client


def test_access_token_is_cached_between_calls() -> None:
    client, http_client = build_client({})

    first = asyncio.run(client.get_access_token())
    second = asyncio.run(client.get_access_token())

    assert first == second == "access-1"
    assert len(http_client.requests) == 1


def test_expired_access_token_is_refreshed() -> None:
    client, http_client = build_client({})
    asyncio.run(client.get_access_token())
    client._access_token_expires_at = datetime(2000, 1, 1, tzinfo=UTC)

    asyncio.run(client.get_access_token())

    assert len(http_client.requests) == 2


def test_rejected_refresh_token_explains_the_testing_status_trap() -> None:
    http_client = FakeAsyncClient({"token": 400})
    client = GmailClient(CREDENTIALS, http_client)

    with pytest.raises(GmailAuthError) as error:
        asyncio.run(client.get_access_token())

    assert "Testing status" in str(error.value)


def test_resolve_label_id_matches_case_insensitively() -> None:
    client, _ = build_client(
        {"/labels": {"labels": [{"id": "Label_9", "name": "Financial/Taxes"}]}}
    )

    assert asyncio.run(client.resolve_label_id("financial/taxes")) == "Label_9"


def test_resolve_label_id_returns_none_when_absent() -> None:
    client, _ = build_client({"/labels": {"labels": [{"id": "Label_1", "name": "Receipts"}]}})

    assert asyncio.run(client.resolve_label_id("financial/taxes")) is None


def test_list_thread_ids_follows_pagination_up_to_the_limit() -> None:
    pages = iter(
        [
            {"threads": [{"id": "t1"}, {"id": "t2"}], "nextPageToken": "page-2"},
            {"threads": [{"id": "t3"}]},
        ]
    )
    client, _ = build_client({"/threads": pages.__next__})

    assert asyncio.run(client.list_thread_ids(["Label_9"], limit=5)) == ["t1", "t2", "t3"]


def test_list_thread_ids_stops_at_the_limit() -> None:
    client, _ = build_client(
        {"/threads": {"threads": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]}}
    )

    assert asyncio.run(client.list_thread_ids(["Label_9"], limit=2)) == ["t1", "t2"]


def test_archiving_only_removes_the_inbox_label() -> None:
    """The thread and its other labels survive: archiving files mail, it does
    not dispose of it."""
    client, http_client = build_client(
        {
            "/modify": {},
            "/threads/t1": {
                "messages": [message("m1", "Digest", "a@x.com", "b@x.com", "1700000000000")]
            },
        }
    )

    asyncio.run(client.modify_thread("t1", remove_label_ids=[INBOX_LABEL_ID]))

    method, url, payload = http_client.requests[1]
    assert (method, url.endswith("/threads/t1/modify")) == ("POST", True)
    assert payload == {"addLabelIds": [], "removeLabelIds": [INBOX_LABEL_ID]}


def test_trashing_uses_the_reversible_thread_endpoint() -> None:
    """`threads.trash` is recoverable; `threads.delete` is never called."""
    trashed = message("m1", "Digest", "a@x.com", "b@x.com", "1700000000000")
    trashed["labelIds"] = [TRASH_LABEL_ID]
    client, http_client = build_client({"/trash": {}, "/threads/t1": {"messages": [trashed]}})

    thread = asyncio.run(client.trash_thread("t1"))

    method, url, payload = http_client.requests[1]
    assert (method, url.endswith("/threads/t1/trash")) == ("POST", True)
    assert payload == {}
    assert thread.label_ids == [TRASH_LABEL_ID]


def test_the_client_cannot_delete_anything() -> None:
    """Gmail's destructive calls are absent rather than guarded."""
    source = (Path(adminos.adapters.gmail.__file__)).read_text()

    assert not hasattr(GmailClient, "delete_thread")
    assert not hasattr(GmailClient, "delete_message")
    assert "messages/{message_id}/delete" not in source
    assert '"DELETE"' not in source


def test_list_thread_ids_sends_every_label() -> None:
    """Gmail ANDs labelIds, which is how INBOX narrows the intake label."""
    client, http_client = build_client({"/threads": {"threads": [{"id": "t1"}]}})

    asyncio.run(client.list_thread_ids([INBOX_LABEL_ID, "Label_9"], limit=5))

    _method, _url, params = http_client.requests[-1]
    assert params is not None
    assert params["labelIds"] == ["INBOX", "Label_9"]


def test_fetch_thread_requests_metadata_only() -> None:
    client, http_client = build_client(
        {
            "/threads/t1": {
                "messages": [
                    message(
                        "m1",
                        "Q3 estimate",
                        "cpa@example.com",
                        "me@example.com",
                        "1700000000000",
                    )
                ]
            }
        }
    )

    asyncio.run(client.fetch_thread("t1"))

    _method, _url, params = http_client.requests[-1]
    assert params is not None
    assert params["format"] == "metadata"


def test_gmail_http_error_is_wrapped() -> None:
    client, _ = build_client({"/labels": 500})

    with pytest.raises(GmailError):
        asyncio.run(client.resolve_label_id("financial/taxes"))


def test_gmail_error_carries_no_response_body() -> None:
    """Upstream text can echo mailbox content, so only the status is surfaced."""
    client, _ = build_client({"/labels": 500})

    with pytest.raises(GmailError) as error:
        asyncio.run(client.resolve_label_id("financial/taxes"))

    assert str(error.value) == "Gmail returned HTTP 500."


def test_build_thread_takes_the_subject_from_the_first_message() -> None:
    payload = {
        "messages": [
            message("m1", "Original", "a@example.com", "me@example.com", "1700000000000"),
            message("m2", "Re: Original", "me@example.com", "a@example.com", "1700000900000"),
        ]
    }

    thread = build_thread("t1", payload)

    assert thread.subject == "Original"
    assert thread.message_id == "m2"
    assert thread.received_at == datetime.fromtimestamp(1700000900, tz=UTC)


def test_build_thread_tolerates_an_empty_thread() -> None:
    thread = build_thread("t1", {})

    assert thread.thread_id == "t1"
    assert thread.subject is None
    assert thread.participants == []


def test_participants_are_deduplicated_and_stripped_of_display_names() -> None:
    messages = [
        message("m1", "s", "CPA <cpa@example.com>", "Me <me@example.com>", "1"),
        message("m2", "s", "Me <ME@example.com>", "cpa@example.com", "2"),
    ]

    assert collect_participants(messages) == ["cpa@example.com", "me@example.com"]


def test_read_internal_date_ignores_junk() -> None:
    assert read_internal_date({"internalDate": "not-a-number"}) is None
    assert read_internal_date({}) is None


def test_content_hash_changes_only_with_content() -> None:
    payload = {
        "messages": [
            message(
                "m1", "Q3 estimate", "cpa@example.com", "me@example.com", "1700000000000", "hi"
            )
        ]
    }
    first = build_thread("t1", payload)
    second = build_thread("t1", payload)

    replied = build_thread(
        "t1",
        {
            "messages": [
                *payload["messages"],
                message("m2", "Re: Q3", "me@example.com", "cpa@example.com", "1700000900000"),
            ]
        },
    )

    assert first.content_hash() == second.content_hash()
    assert first.content_hash() != replied.content_hash()
