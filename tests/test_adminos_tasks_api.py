from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from adminos.adapters.monday import ItemFilter, MondayError, MondayItem
from adminos.api import admin as admin_module
from adminos.db import engine as engine_module
from adminos.db.models import Classification, Evidence
from adminos.domain.classification import Disposition, Relationship


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}
BOARD_ID = "8962223984"
GROUP_ID = "group_mkqmqhnc"


def item(name: str, item_id: str = "900") -> MondayItem:
    return MondayItem(
        item_id=item_id,
        name=name,
        group="Tasks | Action Items",
        status="Not Yet Started",
        admin_os_id=None,
        action_date=None,
        board_id=BOARD_ID,
    )


BOARD = [item("Annual Taxes | KPMG"), item("Taxes | Annual FBAR Filing", "901")]


class FakeReader:
    def __init__(self, items: list[MondayItem]) -> None:
        self.items = items

    async def list_items(
        self,
        board_id: str,
        item_filter: ItemFilter = ItemFilter.OPEN,
        contains: str | None = None,
        limit: int = 500,
    ) -> list[MondayItem]:
        return self.items[:limit]


class FakeWriter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.created: list[dict[str, str | None]] = []

    async def find_by_admin_os_id(self, board_id: str, admin_os_id: str) -> MondayItem | None:
        return None

    async def create_item(
        self,
        board_id: str,
        name: str,
        admin_os_id: str,
        group_id: str | None = None,
        action_date: str | None = None,
    ) -> str:
        if self.error is not None:
            raise self.error
        self.created.append(
            {
                "board_id": board_id,
                "name": name,
                "admin_os_id": admin_os_id,
                "group_id": group_id,
                "action_date": action_date,
            }
        )
        return "12345"

    async def read_item(self, item_id: str) -> MondayItem | None:
        created = self.created[-1]
        return MondayItem(
            item_id=item_id,
            name=str(created["name"]),
            group="Tasks | Action Items",
            status=None,
            admin_os_id=str(created["admin_os_id"]),
            action_date=None,
            board_id=BOARD_ID,
        )


def install_monday(
    monkeypatch: pytest.MonkeyPatch,
    writer: FakeWriter,
    items: list[MondayItem] = BOARD,
) -> None:
    @asynccontextmanager
    async def open_client(_token: str) -> AsyncIterator[FakeReader]:
        yield FakeReader(items)

    @asynccontextmanager
    async def open_writer(_token: str) -> AsyncIterator[FakeWriter]:
        yield writer

    monkeypatch.setattr(admin_module, "open_monday_client", open_client)
    monkeypatch.setattr(admin_module, "open_monday_writer", open_writer)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'tasks-api.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("MONDAY_API_TOKEN", "monday-token")
    monkeypatch.setenv("TODO_BOARD_ID", BOARD_ID)
    monkeypatch.setenv("TODO_GROUP_ID", GROUP_ID)
    monkeypatch.setenv("DATABASE_URL", url)
    engine_module.dispose_connection()
    yield TestClient(main.app)
    engine_module.dispose_connection()


def add_evidence(confidence: float | None = 0.0) -> str:
    factory = sessionmaker(bind=engine_module.get_engine())
    with factory() as session:
        evidence = Evidence(
            source_system="gmail",
            source_thread_id="197b351c69d3613f",
            subject="KPMG Activities",
            participants=["cpa@example.com"],
            received_at=datetime(2026, 3, 1, tzinfo=UTC),
            content_hash="hash-1",
        )
        session.add(evidence)
        session.flush()
        if confidence is not None:
            session.add(
                Classification(
                    evidence_id=evidence.id,
                    classifier_version="test",
                    relationship_type=Relationship.UNDETERMINED,
                    disposition=Disposition.NEEDS_REVIEW,
                    confidence=confidence,
                    requires_review=True,
                )
            )
        session.commit()
        return evidence.id


def test_creating_a_task_requires_a_key(client: TestClient) -> None:
    response = client.post(
        "/admin/monday/tasks",
        json={"evidence_id": "e1", "title": "Taxes | Something"},
    )

    assert response.status_code == 401


def test_an_uncertain_task_is_refused_with_its_duplicate_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = FakeWriter()
    install_monday(monkeypatch, writer)
    evidence_id = add_evidence()

    response = client.post(
        "/admin/monday/tasks",
        json={"evidence_id": evidence_id, "title": "Annual Taxes | KPMG"},
        headers=AUTH,
    )

    assert response.status_code == 409
    assert writer.created == []
    assert response.json()["detail"]["duplicates"]["matches"][0]["name"] == "Annual Taxes | KPMG"


def test_confirmation_creates_and_verifies_the_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = FakeWriter()
    install_monday(monkeypatch, writer)
    evidence_id = add_evidence()

    response = client.post(
        "/admin/monday/tasks",
        json={
            "evidence_id": evidence_id,
            "title": "Taxes | Confirm KPMG scope",
            "confirmed": True,
            "action_date": "2026-08-15",
        },
        headers=AUTH,
    )

    body = response.json()
    assert response.status_code == 200
    assert body["item_id"] == "12345"
    assert body["verified"] is True
    assert body["board_id"] == BOARD_ID
    assert writer.created[0]["group_id"] == GROUP_ID
    assert writer.created[0]["action_date"] == "2026-08-15"
    assert writer.created[0]["admin_os_id"] == body["admin_os_id"]


def test_a_verified_task_leaves_the_review_queue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_monday(monkeypatch, FakeWriter())
    evidence_id = add_evidence()
    assert client.get("/admin/review-queue", headers=AUTH).json()["count"] == 1

    client.post(
        "/admin/monday/tasks",
        json={"evidence_id": evidence_id, "title": "Taxes | Confirm KPMG scope", "confirmed": True},
        headers=AUTH,
    )

    assert client.get("/admin/review-queue", headers=AUTH).json()["count"] == 0


def test_unknown_evidence_is_a_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_monday(monkeypatch, FakeWriter())

    response = client.post(
        "/admin/monday/tasks",
        json={"evidence_id": "missing", "title": "Taxes | Something", "confirmed": True},
        headers=AUTH,
    )

    assert response.status_code == 404


def test_an_empty_title_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    install_monday(monkeypatch, FakeWriter())

    response = client.post(
        "/admin/monday/tasks",
        json={"evidence_id": "e1", "title": "   ", "confirmed": True},
        headers=AUTH,
    )

    assert response.status_code == 422


def test_a_monday_failure_is_reported_as_a_gateway_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_monday(monkeypatch, FakeWriter(error=MondayError("Monday is down.")))
    evidence_id = add_evidence()

    response = client.post(
        "/admin/monday/tasks",
        json={"evidence_id": evidence_id, "title": "Taxes | Confirm scope", "confirmed": True},
        headers=AUTH,
    )

    assert response.status_code == 502


def test_creating_a_task_needs_a_monday_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MONDAY_API_TOKEN", raising=False)

    response = client.post(
        "/admin/monday/tasks",
        json={"evidence_id": "e1", "title": "Taxes | Something", "confirmed": True},
        headers=AUTH,
    )

    assert response.status_code == 503


def test_a_task_can_only_land_on_the_todo_board(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slice is scoped to one board, and the caller cannot redirect it."""
    writer = FakeWriter()
    install_monday(monkeypatch, writer)
    evidence_id = add_evidence()

    client.post(
        "/admin/monday/tasks",
        json={
            "evidence_id": evidence_id,
            "title": "Taxes | Confirm scope",
            "confirmed": True,
            "board_id": "18404353669",
        },
        headers=AUTH,
    )

    assert writer.created[0]["board_id"] == BOARD_ID
