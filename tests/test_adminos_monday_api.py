from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

import main
from adminos.adapters.monday import ItemFilter, MondayAuthError, MondayError, MondayItem
from adminos.api import admin as admin_module


API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}
BOARD_ID = "8962223984"


def item(
    name: str,
    status: str | None = "Not Yet Started",
    admin_os_id: str | None = None,
) -> MondayItem:
    return MondayItem(
        item_id=str(abs(hash(name)) % 10**8),
        name=name,
        group="Tasks | Action Items",
        status=status,
        admin_os_id=admin_os_id,
        action_date=None,
    )


BOARD = [
    item("Annual Taxes | KPMG"),
    item("Taxes | Annual FBAR Filing"),
    item("GS, NYC Visit | Submit Expenses"),
    item("USA Taxes | Complete FBAR", status="Done"),
]


class FakeMondayClient:
    def __init__(self, items: list[MondayItem], error: Exception | None = None) -> None:
        self.items = items
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def list_items(
        self,
        board_id: str,
        item_filter: ItemFilter = ItemFilter.OPEN,
        contains: str | None = None,
        limit: int = 500,
    ) -> list[MondayItem]:
        self.calls.append(
            {
                "board_id": board_id,
                "filter": item_filter,
                "contains": contains,
                "limit": limit,
            }
        )
        if self.error is not None:
            raise self.error
        return self.items[:limit]


def install_client(monkeypatch: pytest.MonkeyPatch, fake: FakeMondayClient) -> None:
    @asynccontextmanager
    async def open_client(_token: str) -> AsyncIterator[FakeMondayClient]:
        yield fake

    monkeypatch.setattr(admin_module, "open_monday_client", open_client)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("MONDAY_API_TOKEN", "monday-token")
    monkeypatch.setenv("TODO_BOARD_ID", BOARD_ID)
    yield TestClient(main.app)


def test_board_read_requires_a_key(client: TestClient) -> None:
    assert client.get("/admin/monday/board").status_code == 401


def test_duplicate_check_requires_a_key(client: TestClient) -> None:
    response = client.post("/admin/monday/duplicate-check", json={"titles": ["Taxes"]})

    assert response.status_code == 401


def test_board_read_needs_a_monday_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MONDAY_API_TOKEN", raising=False)

    response = client.get("/admin/monday/board", headers=AUTH)

    assert response.status_code == 503


def test_board_read_needs_a_board_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TODO_BOARD_ID", raising=False)

    response = client.get("/admin/monday/board", headers=AUTH)

    assert response.status_code == 503


def test_board_read_returns_items(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    install_client(monkeypatch, FakeMondayClient(BOARD))

    response = client.get("/admin/monday/board", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["board_id"] == BOARD_ID
    assert body["count"] == len(BOARD)
    assert body["items"][0]["name"] == "Annual Taxes | KPMG"


def test_board_read_defaults_to_open_items(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMondayClient(BOARD)
    install_client(monkeypatch, fake)

    client.get("/admin/monday/board", headers=AUTH)

    assert fake.calls[0]["filter"] == ItemFilter.OPEN


def test_board_read_passes_the_filter_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMondayClient(BOARD)
    install_client(monkeypatch, fake)

    response = client.get(
        "/admin/monday/board",
        params={"filter": "done", "contains": "KPMG", "limit": 5},
        headers=AUTH,
    )

    assert response.status_code == 200
    assert fake.calls[0] == {
        "board_id": BOARD_ID,
        "filter": ItemFilter.DONE,
        "contains": "KPMG",
        "limit": 5,
    }


def test_board_read_rejects_an_unknown_filter(client: TestClient) -> None:
    response = client.get("/admin/monday/board", params={"filter": "urgent"}, headers=AUTH)

    assert response.status_code == 422


def test_board_read_reads_only_the_todo_board(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slice is scoped to one board; no route may reach the GS board."""
    fake = FakeMondayClient(BOARD)
    install_client(monkeypatch, fake)

    client.get("/admin/monday/board", params={"list": "gs"}, headers=AUTH)

    assert fake.calls[0]["board_id"] == BOARD_ID


def test_a_monday_failure_is_reported_as_a_gateway_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_client(monkeypatch, FakeMondayClient([], error=MondayError("Monday is down.")))

    response = client.get("/admin/monday/board", headers=AUTH)

    assert response.status_code == 502


def test_a_rejected_token_is_reported_rather_than_swallowed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_client(monkeypatch, FakeMondayClient([], error=MondayAuthError("bad token")))

    response = client.get("/admin/monday/board", headers=AUTH)

    assert response.status_code == 502


def test_duplicate_check_reports_an_existing_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_client(monkeypatch, FakeMondayClient(BOARD))

    response = client.post(
        "/admin/monday/duplicate-check",
        json={"titles": ["Annual Taxes | KPMG"]},
        headers=AUTH,
    )

    assert response.status_code == 200
    report = response.json()["reports"][0]
    assert report["has_strong_match"] is True
    assert report["matches"][0]["name"] == "Annual Taxes | KPMG"
    assert report["matches"][0]["is_strong"] is True


def test_duplicate_check_handles_several_candidates_at_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_client(monkeypatch, FakeMondayClient(BOARD))

    response = client.post(
        "/admin/monday/duplicate-check",
        json={"titles": ["Annual Taxes | KPMG", "Renew the car registration"]},
        headers=AUTH,
    )

    reports = response.json()["reports"]
    assert [report["has_strong_match"] for report in reports] == [True, False]


def test_duplicate_check_compares_against_completed_work_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMondayClient(BOARD)
    install_client(monkeypatch, fake)

    client.post("/admin/monday/duplicate-check", json={"titles": ["Taxes"]}, headers=AUTH)

    assert fake.calls[0]["filter"] == ItemFilter.ALL


def test_duplicate_check_can_be_narrowed_to_open_work(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMondayClient(BOARD)
    install_client(monkeypatch, fake)

    client.post(
        "/admin/monday/duplicate-check",
        json={"titles": ["Taxes"], "filter": "open"},
        headers=AUTH,
    )

    assert fake.calls[0]["filter"] == ItemFilter.OPEN


def test_duplicate_check_reports_how_much_it_compared(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_client(monkeypatch, FakeMondayClient(BOARD))

    response = client.post(
        "/admin/monday/duplicate-check",
        json={"titles": ["Annual Taxes | KPMG"]},
        headers=AUTH,
    )

    assert response.json()["compared"] == len(BOARD)


def test_duplicate_check_rejects_an_empty_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_client(monkeypatch, FakeMondayClient(BOARD))

    response = client.post(
        "/admin/monday/duplicate-check",
        json={"titles": ["   "]},
        headers=AUTH,
    )

    assert response.status_code == 422


def test_duplicate_check_rejects_an_oversized_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_client(monkeypatch, FakeMondayClient(BOARD))

    response = client.post(
        "/admin/monday/duplicate-check",
        json={"titles": [f"Task {index}" for index in range(50)]},
        headers=AUTH,
    )

    assert response.status_code == 422


def test_duplicate_check_creates_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewing for duplicates must never touch the board."""
    fake = FakeMondayClient(BOARD)
    install_client(monkeypatch, fake)

    client.post(
        "/admin/monday/duplicate-check",
        json={"titles": ["Annual Taxes | KPMG", "Renew the car registration"]},
        headers=AUTH,
    )

    assert fake.calls == [
        {"board_id": BOARD_ID, "filter": ItemFilter.ALL, "contains": None, "limit": 1500}
    ]
