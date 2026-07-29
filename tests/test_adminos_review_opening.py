"""A review says what it is before it shows anything.

Whatever the words that start a morning — "good morning", "check my inbox",
"pick up where we left off" — they all arrive here as a start, a continue or a
restart, and each of those opens by saying what will be worked through, that it
follows a playbook Brian and the agent hold together, and that the playbook
changes by agreement. The words are Admin OS's, so that they can be versioned
with the workflow they describe and tested rather than remembered.
"""

from pathlib import Path
from typing import Any, Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import main
from adminos.capabilities.config import OPENING_NEW, OPENING_RESUMED, clear_cache
from adminos.db import engine as engine_module
from tests.test_adminos_review_api import (
    API_KEY,
    AUTH,
    CONFIG_PATH,
    REPOSITORY_ROOT,
    begin,
    mailbox,
    proposed,
    start,
    thread,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'review-opening.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("CAPABILITIES_PATH", str(CONFIG_PATH))
    clear_cache()
    engine_module.dispose_connection()

    yield TestClient(main.app)

    engine_module.dispose_connection()
    clear_cache()


def opening_of(body: dict[str, Any]) -> dict[str, Any] | None:
    return body["plan"]["opening"]


def decide(client: TestClient, body: dict[str, Any], decision: str) -> dict[str, Any]:
    item_id = body["current_group"]["items"][0]["item_id"]
    response = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": decision},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_new_review_opens_by_saying_what_it_is(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Good morning" is answered with the morning, not with "how can I help?"."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    opening = opening_of(proposed(client))

    assert opening is not None
    assert opening["mode"] == "new"
    assert opening["text"] == OPENING_NEW
    assert opening["text"].startswith("**Here's what we'll work through together.**")
    assert "our admin playbook" in opening["text"]
    assert "I'll guide you through each step" in opening["text"]
    assert "evolve the playbook together" in opening["text"]


def test_a_resumed_review_opens_by_saying_what_happens_next(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Picking up where he left off is a different promise from starting."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    decide(client, start(client), "defer")

    opening = opening_of(
        client.post("/review/continue", headers=AUTH, json={"sync": False}).json()
    )

    assert opening is not None
    assert opening["mode"] == "resumed"
    assert opening["text"] == OPENING_RESUMED
    assert opening["text"].startswith("**Here's what we'll do next.**")
    assert "continue from where we left off" in opening["text"]
    assert "finish the current playbook" in opening["text"]
    assert "continue improving the playbook" in opening["text"]


def test_starting_over_a_review_under_way_opens_a_new_one_in_words_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Start my admin" takes a fresh snapshot, and the words say a new one."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    worked = start(client)
    decide(client, worked, "defer")

    again = client.post("/review/start", headers=AUTH, json={"sync": False}).json()

    assert again["review_id"] != worked["review_id"]
    assert opening_of(again) == {"mode": "new", "text": OPENING_NEW}


def test_a_review_that_exists_but_settled_nothing_is_still_new(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What makes a morning resumable is work in it, not a row in a table."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    proposed(client)

    again = client.post("/review/start", headers=AUTH, json={"sync": False}).json()

    assert opening_of(again) == {"mode": "new", "text": OPENING_NEW}


def test_restarting_opens_the_fresh_review_as_a_new_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    decide(client, start(client), "defer")

    restarted = client.post("/review/restart", headers=AUTH, json={"sync": False}).json()

    assert opening_of(restarted) == {"mode": "new", "text": OPENING_NEW}


def test_the_opening_is_not_said_again_inside_the_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Said once on entry and structurally unavailable after it.

    Repeating the playbook before every group is a thing a caller can only do
    with words it was given twice, so it is given them once: beginning the
    plan, deciding a row and reading the review back carry no opening at all.
    """
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    entered = proposed(client)
    run_id = entered["run_id"]

    begun = begin(client, run_id)
    decided = decide(client, begun, "dismiss")
    read = client.get(f"/review/runs/{run_id}", headers=AUTH).json()

    assert opening_of(entered) is not None
    assert opening_of(begun) is None
    assert opening_of(decided["run"]) is None
    assert opening_of(read) is None


def test_moving_to_the_next_group_says_nothing_about_the_playbook(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One uninterrupted review, two groups, one opening."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    body = start(client)

    first = decide(client, body, "dismiss")
    assert first["run"]["plan"]["current"] == "admin"

    second = decide(client, first["run"], "dismiss")

    assert opening_of(first["run"]) is None
    assert opening_of(second["run"]) is None


def test_the_opening_is_configuration_and_moves_with_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The words belong to the workflow, not to whatever is doing the talking."""
    edited = tmp_path / "capabilities.yaml"
    edited.write_text(
        CONFIG_PATH.read_text().replace(
            "channel: email",
            'channel: email\nopening:\n  new: "Mornings run to a playbook."\n'
            '  resumed: "Carrying on with the playbook."',
        )
    )
    monkeypatch.setenv("CAPABILITIES_PATH", str(edited))
    clear_cache()
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    opening = opening_of(proposed(client))

    assert opening == {"mode": "new", "text": "Mornings run to a playbook."}
