"""Checking the Monday scope is something Brian can do before a review runs.

Configuration that is only exercised in the middle of a morning is
configuration whose mistakes are found in the middle of a morning. This route
reads the playbook's scope, holds it against the live board, and answers with
either what it takes or what is missing — writing nothing either way.
"""

from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import pytest
import yaml

from alembic import command
from alembic.config import Config
from contextlib import asynccontextmanager
from fastapi.testclient import TestClient

import main
from adminos.adapters.monday import MondayColumn, MondayItem
from adminos.api import admin as admin_module
from adminos.capabilities.config import clear_cache
from adminos.db import engine as engine_module
from tests.test_adminos_review_api import API_KEY, AUTH, CONFIG_PATH, REPOSITORY_ROOT
from tests.test_adminos_board_scope import (
    BOARD_ID,
    BOARD_NAME,
    CADENCE_COLUMN,
    CADENCE_SETTINGS,
    TODAY_COLUMN,
    TODAY_SETTINGS,
)


SOURCE_PLAYBOOK = REPOSITORY_ROOT / "tests/data/playbook_pair.yaml"

COLUMNS = [
    MondayColumn(TODAY_COLUMN, "Status", "status", TODAY_SETTINGS),
    MondayColumn(CADENCE_COLUMN, "Cadence", "status", CADENCE_SETTINGS),
]


class FakeBoard:
    """A Monday that answers about its shape and its scoped items, and no more."""

    def __init__(self, columns: list[MondayColumn], items: list[MondayItem]) -> None:
        self.columns = columns
        self.items = items
        self.reads: list[dict[str, Any]] = []

    async def read_board(self, board_id: str) -> tuple[str, list[MondayColumn]]:
        return BOARD_NAME, self.columns

    async def list_items_matching(
        self,
        board_id: str,
        rules: list[dict[str, Any]],
        column_ids: list[str],
        operator: str = "or",
        limit: int = 500,
    ) -> list[MondayItem]:
        self.reads.append({"rules": rules, "operator": operator, "limit": limit})
        return self.items


def item(name: str, today: str | None = None, cadence: str | None = None) -> MondayItem:
    return MondayItem(
        item_id=str(abs(hash(name)) % 10**8),
        name=name,
        group="Tasks | Action Items",
        status=today,
        admin_os_id=None,
        action_date=None,
        values={TODAY_COLUMN: today, CADENCE_COLUMN: cadence},
    )


def install(monkeypatch: pytest.MonkeyPatch, board: FakeBoard) -> None:
    @asynccontextmanager
    async def open_client(_token: str) -> AsyncIterator[FakeBoard]:
        yield board

    monkeypatch.setattr(admin_module, "open_monday_client", open_client)


def playbook_with(sources: dict[str, Any] | None, path: Path) -> Path:
    document = yaml.safe_load(SOURCE_PLAYBOOK.read_text())
    if sources is not None:
        document["sources"] = sources
    path.write_text(yaml.safe_dump(document))
    return path


def make_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, playbook: Path
) -> TestClient:
    url = f"sqlite:///{tmp_path / 'board-scope.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("MONDAY_API_TOKEN", "monday-token")
    monkeypatch.setenv("CAPABILITIES_PATH", str(CONFIG_PATH))
    monkeypatch.setenv("ASSISTANT_PLAYBOOK_PATH", str(playbook))
    clear_cache()
    engine_module.dispose_connection()
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    engine_module.dispose_connection()
    clear_cache()


def scoped_playbook(tmp_path: Path, labels: list[str] | None = None) -> Path:
    return playbook_with(
        {
            "monday": {
                "board_id": BOARD_ID,
                "filters": [
                    {"column_id": TODAY_COLUMN, "labels": ["Working on it today"]},
                    {"column_id": CADENCE_COLUMN, "labels": labels or ["Daily"]},
                ],
            }
        },
        tmp_path / "playbook.yaml",
    )


def test_reading_the_scope_requires_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client(tmp_path, monkeypatch, scoped_playbook(tmp_path))

    assert client.get("/admin/monday/scope").status_code == 401


def test_a_playbook_naming_no_board_reviews_no_monday_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unconfigured is answered as unconfigured, not as an empty board."""
    playbook = playbook_with(None, tmp_path / "playbook.yaml")
    client = make_client(tmp_path, monkeypatch, playbook)

    body = client.get("/admin/monday/scope", headers=AUTH).json()

    assert body["configured"] is False
    assert body["resolved"] is False
    assert body["items"] is None
    assert "names no Monday board" in body["detail"]


def test_a_resolved_scope_reports_what_it_takes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board = FakeBoard(
        COLUMNS,
        [
            item("Renew domain", today="Working on it today"),
            item("Morning review", cadence="Daily"),
        ],
    )
    install(monkeypatch, board)
    client = make_client(tmp_path, monkeypatch, scoped_playbook(tmp_path))

    body = client.get("/admin/monday/scope", headers=AUTH).json()

    assert body["configured"] is True
    assert body["resolved"] is True
    assert body["board_name"] == BOARD_NAME
    assert body["items"] == 2
    assert body["describes"] == (
        "Items on To Do List where Status is 'Working on it today' or "
        "Cadence is 'Daily'."
    )
    assert [filter["indexes"] for filter in body["filters"]] == [[2], [5]]
    assert board.reads[0]["operator"] == "or"


def test_a_label_the_board_lacks_is_reported_rather_than_widened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board is not read at all, because there is nothing exact to read."""
    board = FakeBoard(COLUMNS, [item("Renew domain", today="Working on it today")])
    install(monkeypatch, board)
    client = make_client(tmp_path, monkeypatch, scoped_playbook(tmp_path, ["Every day"]))

    body = client.get("/admin/monday/scope", headers=AUTH).json()

    assert body["resolved"] is False
    assert body["items"] is None
    assert "no label 'Every day'" in body["detail"]
    assert board.reads == []
