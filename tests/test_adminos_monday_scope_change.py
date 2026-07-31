"""Configuring the Monday scope is a change to the playbook, checked on the board.

Two failures are guarded here and they pull in opposite directions. A scope
nobody can change conversationally is a scope that lives in a YAML file Brian
cannot reach. A scope changed on his word alone is a filter that may name a
column Monday does not have — which Monday answers by matching nothing, and
matching nothing on a thousand-item board reads exactly like today's work. So
the change goes through propose and confirm like every other, and proposing is
where the board is asked whether the columns and labels are really there.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest

from fastapi.testclient import TestClient

from adminos.api import playbook as playbook_module
from adminos.capabilities.config import CapabilityConfig, LoadedCapabilities, clear_cache
from adminos.db import engine as engine_module
from adminos.domain.playbook import (
    ChangeRefused,
    ClearMondayScope,
    PlaybookDocument,
    apply_changes,
    parse_playbook,
    read_change,
)
from tests.conftest import build_capability
from tests.test_adminos_board_scope import BOARD_ID, CADENCE_COLUMN, TODAY_COLUMN
from tests.test_adminos_board_scope_api import (
    COLUMNS,
    FakeBoard,
    make_client,
    playbook_with,
)
from tests.test_adminos_review_api import AUTH, REPOSITORY_ROOT


LIVE_PLAYBOOK = REPOSITORY_ROOT / "config/assistant-playbook.yaml"
TEST_PLAYBOOK = REPOSITORY_ROOT / "tests/data/playbook_pair.yaml"

TODAY_SCOPE = {
    "operation": "set_monday_scope",
    "board_id": BOARD_ID,
    "filters": [
        {"column_id": TODAY_COLUMN, "labels": ["Working on it today"]},
        {"column_id": CADENCE_COLUMN, "labels": ["Daily"]},
    ],
}


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    engine_module.dispose_connection()
    clear_cache()


def capabilities() -> LoadedCapabilities:
    loaded: tuple[CapabilityConfig, ...] = tuple(
        build_capability(key=key, position=position * 10)
        for position, key in enumerate(("admin", "financial_taxes"), start=1)
    )
    return LoadedCapabilities(
        version="test.1", digest="d" * 64, channel="email", capabilities=loaded
    )


def document() -> PlaybookDocument:
    return parse_playbook(TEST_PLAYBOOK.read_bytes())


def test_setting_the_scope_names_the_board_the_columns_and_the_labels() -> None:
    """Every part is what was asked for, and the sentence says which."""
    changed = apply_changes(document(), [read_change(TODAY_SCOPE)], capabilities())

    scope = changed.document.sources.monday
    assert scope is not None
    assert scope.board_id == BOARD_ID
    assert scope.match == "any"
    assert [(one.column_id, one.labels) for one in scope.filters] == [
        (TODAY_COLUMN, ["Working on it today"]),
        (CADENCE_COLUMN, ["Daily"]),
    ]
    assert changed.summary == (
        f"Monday work in scope is items on board {BOARD_ID} where status is "
        f"'Working on it today' or {CADENCE_COLUMN} is 'Daily'.",
    )


def test_setting_the_scope_again_replaces_it_rather_than_adding_to_it() -> None:
    """A second answer to "which items?" is the answer, not another one."""
    narrowed = {**TODAY_SCOPE, "filters": [{"column_id": TODAY_COLUMN, "labels": ["Done"]}]}

    changed = apply_changes(
        document(),
        [read_change(TODAY_SCOPE), read_change(narrowed)],
        capabilities(),
    )

    scope = changed.document.sources.monday
    assert scope is not None
    assert [one.column_id for one in scope.filters] == [TODAY_COLUMN]
    assert scope.filters[0].labels == ["Done"]


def test_clearing_the_scope_leaves_no_monday_work_in_scope() -> None:
    changed = apply_changes(
        document(),
        [read_change(TODAY_SCOPE), ClearMondayScope(operation="clear_monday_scope")],
        capabilities(),
    )

    assert changed.document.sources.monday is None
    assert changed.summary[1] == (
        f"Monday board {BOARD_ID} is no longer reviewed, and no Monday work is in scope."
    )


def test_clearing_a_scope_that_was_never_set_is_refused() -> None:
    """Nothing to clear is said, rather than reported as a change that happened."""
    with pytest.raises(ChangeRefused, match="nothing to clear"):
        apply_changes(
            document(), [ClearMondayScope(operation="clear_monday_scope")], capabilities()
        )


def test_a_scope_with_no_filters_is_not_a_scope() -> None:
    """A board and no columns is the whole board, which is never what is meant."""
    with pytest.raises(ChangeRefused):
        read_change({**TODAY_SCOPE, "filters": []})


def test_a_board_id_that_is_not_a_board_id_is_refused() -> None:
    with pytest.raises(ChangeRefused):
        read_change({**TODAY_SCOPE, "board_id": "To Do List"})


def test_the_shipped_playbook_names_the_live_board_exactly() -> None:
    """The file a fresh database starts from carries the ids read off Monday.

    Titles are what Brian says and ids are what Monday filters by, so a
    Cadence column renamed tomorrow keeps working and a column id typed from
    memory does not.
    """
    shipped = parse_playbook(LIVE_PLAYBOOK.read_bytes())

    scope = shipped.sources.monday
    assert scope is not None
    assert scope.board_id == "8962223984"
    assert scope.match == "any"
    assert [(one.column_id, one.labels) for one in scope.filters] == [
        ("status", ["Working on it today"]),
        ("color_mm5sa3g0", ["Daily"]),
    ]


def install(monkeypatch: pytest.MonkeyPatch, board: FakeBoard) -> None:
    @asynccontextmanager
    async def open_client(_token: str) -> AsyncIterator[FakeBoard]:
        yield board

    monkeypatch.setattr(playbook_module, "open_monday_client", open_client)


def unscoped_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_client(tmp_path, monkeypatch, playbook_with(None, tmp_path / "playbook.yaml"))


def test_a_proposed_scope_the_board_carries_can_be_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proposed, read back, confirmed — and then it is what a session reads."""
    board = FakeBoard(COLUMNS, [])
    install(monkeypatch, board)
    client = unscoped_client(tmp_path, monkeypatch)

    proposed = client.post(
        "/playbook/propose", headers=AUTH, json={"changes": [TODAY_SCOPE]}
    )
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["playbook"]["monday_scope"]["board_id"] == BOARD_ID
    assert client.get("/playbook", headers=AUTH).json()["playbook"]["monday_scope"] is None

    revision_id = body["revision"]["revision_id"]
    confirmed = client.post(
        f"/playbook/revisions/{revision_id}/confirm", headers=AUTH, json={"confirm": True}
    )
    assert confirmed.status_code == 200, confirmed.text

    scope = client.get("/playbook", headers=AUTH).json()["playbook"]["monday_scope"]
    assert scope["board_id"] == BOARD_ID
    assert [one["column_id"] for one in scope["filters"]] == [TODAY_COLUMN, CADENCE_COLUMN]


def test_a_label_the_board_does_not_carry_is_refused_at_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board is asked while the mistake can still be corrected.

    And nothing is written down: a proposal recorded for a scope that cannot
    exist is a revision waiting to be confirmed into a filter matching nothing.
    """
    board = FakeBoard(COLUMNS, [])
    install(monkeypatch, board)
    client = unscoped_client(tmp_path, monkeypatch)
    client.get("/playbook", headers=AUTH)
    typo = {
        **TODAY_SCOPE,
        "filters": [{"column_id": CADENCE_COLUMN, "labels": ["Every day"]}],
    }

    refused = client.post("/playbook/propose", headers=AUTH, json={"changes": [typo]})

    assert refused.status_code == 400
    detail = refused.json()["detail"]
    assert "Every day" in detail and "Daily" in detail
    revisions = client.get("/playbook/revisions", headers=AUTH).json()["revisions"]
    assert [one["status"] for one in revisions] == ["active"]


def test_a_column_the_board_does_not_have_is_refused_at_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board = FakeBoard(COLUMNS, [])
    install(monkeypatch, board)
    client = unscoped_client(tmp_path, monkeypatch)
    wrong = {**TODAY_SCOPE, "filters": [{"column_id": "color_gone", "labels": ["Daily"]}]}

    refused = client.post("/playbook/propose", headers=AUTH, json={"changes": [wrong]})

    assert refused.status_code == 400
    assert "color_gone" in refused.json()["detail"]


def test_a_scope_that_cannot_be_checked_is_not_proposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Monday token is no way to know, and a guess is worse than a refusal."""
    client = unscoped_client(tmp_path, monkeypatch)
    client.get("/playbook", headers=AUTH)
    monkeypatch.delenv("MONDAY_API_TOKEN")

    refused = client.post("/playbook/propose", headers=AUTH, json={"changes": [TODAY_SCOPE]})

    assert refused.status_code == 503
    assert "MONDAY_API_TOKEN" in refused.json()["detail"]
    revisions = client.get("/playbook/revisions", headers=AUTH).json()["revisions"]
    assert [one["status"] for one in revisions] == ["active"]


def test_a_change_that_is_not_about_monday_does_not_read_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reordering a morning is not a reason to call Monday."""
    board = FakeBoard(COLUMNS, [])
    install(monkeypatch, board)
    client = unscoped_client(tmp_path, monkeypatch)

    proposed = client.post(
        "/playbook/propose",
        headers=AUTH,
        json={
            "changes": [
                {"operation": "disable_activity", "activity_key": "email_review"},
            ]
        },
    )

    assert proposed.status_code == 200, proposed.text
    assert board.reads == []


def test_clearing_the_scope_needs_no_board_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewing no Monday work is answerable without asking Monday anything."""
    scoped = playbook_with(
        {
            "monday": {
                "board_id": BOARD_ID,
                "filters": [{"column_id": TODAY_COLUMN, "labels": ["Working on it today"]}],
            }
        },
        tmp_path / "playbook.yaml",
    )
    client = make_client(tmp_path, monkeypatch, scoped)
    monkeypatch.delenv("MONDAY_API_TOKEN")

    proposed = client.post(
        "/playbook/propose",
        headers=AUTH,
        json={"changes": [{"operation": "clear_monday_scope"}]},
    )

    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["playbook"]["monday_scope"] is None


def test_the_scope_a_session_reads_is_the_one_pinned_when_it_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An earlier revision still says what it said, scope included."""
    board = FakeBoard(COLUMNS, [])
    install(monkeypatch, board)
    client = unscoped_client(tmp_path, monkeypatch)

    before = client.get("/playbook", headers=AUTH).json()["revision"]["revision_id"]
    proposal = client.post(
        "/playbook/propose", headers=AUTH, json={"changes": [TODAY_SCOPE]}
    ).json()
    client.post(
        f"/playbook/revisions/{proposal['revision']['revision_id']}/confirm",
        headers=AUTH,
        json={"confirm": True},
    )

    earlier = client.get(f"/playbook/revisions/{before}", headers=AUTH).json()
    assert earlier["playbook"]["monday_scope"] is None
