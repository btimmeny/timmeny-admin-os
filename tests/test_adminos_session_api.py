"""Waking up: the whole session, from the door.

These are the acceptance tests for the thing Brian asked for — "good morning"
answered with a plan, in the playbook's order, read out in Admin OS's own
words, with the mail read afresh and nothing worked until he says to begin.
"""

from pathlib import Path
from typing import Any, Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import main
from adminos.capabilities.config import clear_cache
from adminos.db import engine as engine_module
from tests.test_adminos_review_api import (
    API_KEY,
    AUTH,
    CONFIG_PATH,
    REPOSITORY_ROOT,
    mailbox,
    thread,
)


PLAYBOOK_PATH = REPOSITORY_ROOT / "tests/data/playbook_pair.yaml"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'session-api.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("CAPABILITIES_PATH", str(CONFIG_PATH))
    monkeypatch.setenv("ASSISTANT_PLAYBOOK_PATH", str(PLAYBOOK_PATH))
    clear_cache()
    engine_module.dispose_connection()

    yield TestClient(main.app)

    engine_module.dispose_connection()
    clear_cache()


def post(client: TestClient, path: str, **body: Any) -> dict[str, Any]:
    response = client.post(path, headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def get(client: TestClient, path: str) -> dict[str, Any]:
    response = client.get(path, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def morning(client: TestClient, monkeypatch: pytest.MonkeyPatch, **body: Any) -> dict[str, Any]:
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    return post(client, "/session/start", sync=True, **body)


def test_starting_a_session_requires_authentication(client: TestClient) -> None:
    assert client.post("/session/start", json={}).status_code == 401


def test_good_morning_states_the_plan_and_presents_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the session: what we will do, before any of it."""
    body = morning(client, monkeypatch)

    assert body["status"] == "proposed"
    assert body["review"] is not None
    assert body["review"]["current_group"] is None
    assert body["plan"]["working"] == ["email_review", "session_closeout"]
    assert body["plan"]["current"] is None
    assert body["prompt"]["choices"][0]["operation"] == "beginSession"


def test_the_plan_is_read_out_in_the_order_the_playbook_configures(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = morning(client, monkeypatch)
    said = body["plan"]["message"]

    assert said.startswith("Here's our plan for this session:")
    assert "First, we'll review Email." in said
    assert "Finally, we'll review Closeout." in said
    assert "We'll start with Email." in said
    assert "Within Email, we'll review Admin and Financial/Taxes." in said


def test_the_opening_is_admin_os_words_and_only_at_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeating the orientation between activities is not a rule to remember."""
    body = morning(client, monkeypatch)

    assert body["opening"]["mode"] == "new"
    assert body["opening"]["text"].startswith("**Here's what we'll work through together.**")
    begun = post(client, f"/session/{body['session_id']}/begin")
    assert begun["opening"] is None
    assert get(client, f"/session/{body['session_id']}")["opening"] is None


def test_continuing_a_session_opens_with_the_resumed_words(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = morning(client, monkeypatch)

    resumed = post(client, "/session/continue")

    assert resumed["session_id"] == opened["session_id"]
    assert resumed["opening"]["mode"] == "resumed"
    assert resumed["opening"]["text"].startswith("**Here's what we'll do next.**")


def test_continuing_with_no_session_says_so_rather_than_starting_one(
    client: TestClient,
) -> None:
    response = client.post("/session/continue", headers=AUTH, json={})

    assert response.status_code == 404
    assert "startSession" in response.json()["detail"]


def test_an_activity_that_is_not_built_is_named_and_not_pretended(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = morning(client, monkeypatch)
    objectives = next(
        activity
        for activity in body["plan"]["activities"]
        if activity["activity_key"] == "objectives_review"
    )

    assert body["plan"]["unavailable"] == ["objectives_review"]
    assert objectives["state"] == "unavailable"
    assert objectives["availability"] == "planned"
    assert objectives["data_source"] == "monday"
    assert "not built here yet" in body["plan"]["message"]
    assert body["playbook"]["validation"]["valid"] is True
    assert body["playbook"]["validation"]["warnings"][0]["code"] == "ACTIVITY_NOT_BUILT"


def test_beginning_the_session_works_the_first_activity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = morning(client, monkeypatch)

    begun = post(client, f"/session/{opened['session_id']}/begin")

    assert begun["status"] == "in_progress"
    assert begun["plan"]["current"] == "email_review"
    assert begun["plan"]["activity_number"] == 1
    assert begun["plan"]["activity_count"] == 2
    assert begun["review"]["current_group"]["capability_key"] == "admin"


def test_a_session_only_order_leaves_the_playbook_alone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Closeout first, today" must not become how every morning works."""
    body = morning(client, monkeypatch, order=["session_closeout"])

    assert body["plan"]["working"] == ["session_closeout", "email_review"]
    assert body["plan"]["overrides"][0].endswith("which is not the playbook's order.")
    playbook = get(client, "/playbook")
    assert [activity["activity_key"] for activity in playbook["playbook"]["activities"]] == [
        "email_review",
        "objectives_review",
        "session_closeout",
    ]


def test_skipping_an_activity_today_is_recorded_on_the_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = morning(client, monkeypatch, skip=["email_review"])

    assert body["plan"]["skipped"] == ["email_review"]
    assert body["plan"]["overrides"] == ["Email is set aside for this session only."]
    assert body["review"] is None
    assert get(client, "/playbook")["playbook"]["activities"][0]["enabled"] is True


def test_saying_hello_again_reads_the_mailbox_and_sets_the_first_session_aside(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = morning(client, monkeypatch)

    second = post(client, "/session/start", sync=True)

    assert second["session_id"] != first["session_id"]
    assert second["supersedes_session_id"] == first["session_id"]
    assert second["review"]["review_id"] != first["review"]["review_id"]
    assert second["review"]["snapshot_at"] is not None


def test_a_change_to_the_playbook_is_proposed_before_it_is_anything(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Brian says it, Admin OS writes down what it would do, Brian confirms."""
    morning(client, monkeypatch)

    proposal = post(
        client,
        "/playbook/propose",
        changes=[
            {
                "operation": "move_activity",
                "activity_key": "session_closeout",
                "before_activity_key": "email_review",
            }
        ],
        rationale="Finish first, apparently.",
    )

    assert proposal["revision"]["status"] == "proposed"
    assert proposal["effect"] == ["Closeout moves before Email."]
    assert proposal["order_now"] == ["Email", "Objectives", "Closeout"]
    assert proposal["order_after"] == ["Closeout", "Email", "Objectives"]
    assert proposal["confirm_action"]["path"].endswith("/confirm")
    assert get(client, "/playbook")["revision"]["number"] == 1


def test_a_confirmed_change_is_the_playbook_the_next_session_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    morning(client, monkeypatch)
    proposal = post(
        client,
        "/playbook/propose",
        changes=[{"operation": "disable_activity", "activity_key": "objectives_review"}],
    )

    confirmed = post(
        client, proposal["confirm_action"]["path"].lstrip("/") and proposal["confirm_action"]["path"], confirm=True
    )
    later = post(client, "/session/start", sync=False)

    assert confirmed["revision"]["status"] == "active"
    assert confirmed["revision"]["number"] == 2
    assert "objectives_review" not in [
        activity["activity_key"] for activity in later["plan"]["activities"]
    ]
    assert [revision["status"] for revision in get(client, "/playbook/revisions")["revisions"]] == [
        "active",
        "superseded",
    ]


def test_confirming_without_saying_so_changes_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    morning(client, monkeypatch)
    proposal = post(
        client,
        "/playbook/propose",
        changes=[{"operation": "disable_activity", "activity_key": "objectives_review"}],
    )

    response = client.post(
        proposal["confirm_action"]["path"], headers=AUTH, json={"confirm": False}
    )

    assert response.status_code == 400
    assert get(client, "/playbook")["revision"]["number"] == 1


def test_a_change_naming_a_capability_nobody_has_is_refused_by_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    morning(client, monkeypatch)

    proposal = post(
        client,
        "/playbook/propose",
        changes=[
            {
                "operation": "add_step",
                "activity_key": "email_review",
                "capability_key": "legal",
                "label": "Legal",
            }
        ],
    )

    assert proposal["validation"]["valid"] is False
    error = proposal["validation"]["errors"][0]
    assert error["code"] == "UNKNOWN_CAPABILITY"
    assert "legal" in error["message"]
    assert client.post(
        proposal["confirm_action"]["path"], headers=AUTH, json={"confirm": True}
    ).status_code == 409


def settle(
    client: TestClient, session_id: str, run_id: str, **decision: Any
) -> dict[str, Any]:
    """Decide every row the review presents, a group at a time.

    A group holding decisions the mailbox has not seen is presented again
    rather than passed, so this stops at a group it has already answered
    instead of answering it forever.
    """
    answered: set[str] = set()
    body = get(client, f"/session/{session_id}")
    group = body["review"]["current_group"]
    while group is not None and group["capability_key"] not in answered:
        answered.add(group["capability_key"])
        for item in group["items"]:
            response = client.post(
                f"/review/runs/{run_id}/items/{item['item_id']}/decision",
                headers=AUTH,
                json=decision,
            )
            assert response.status_code == 200, response.text
        body = get(client, f"/session/{session_id}")
        group = body["review"]["current_group"]
    return body


def test_a_session_owing_the_mailbox_does_not_move_past_the_email_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deciding is not doing, and a session cannot leave a review that owes."""
    opened = morning(client, monkeypatch)
    session_id = opened["session_id"]
    begun = post(client, f"/session/{session_id}/begin")
    settle(
        client,
        session_id,
        begun["review"]["review_id"],
        decision="override",
        action="gmail.archive",
    )

    advanced = post(client, f"/session/{session_id}/advance")

    assert advanced["plan"]["current"] == "email_review"
    assert advanced["review"]["outstanding_execution"] != []
    email = next(
        activity
        for activity in advanced["plan"]["activities"]
        if activity["activity_key"] == "email_review"
    )
    assert email["state"] == "in_progress"


def test_the_closeout_counts_verified_work_and_names_the_session_finished(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = morning(client, monkeypatch)
    session_id = opened["session_id"]
    begun = post(client, f"/session/{session_id}/begin")
    settle(client, session_id, begun["review"]["review_id"], decision="dismiss")

    advanced = post(client, f"/session/{session_id}/advance")
    closeout = post(client, f"/session/{session_id}/begin")
    finished = post(client, f"/session/{session_id}/advance")

    assert advanced["plan"]["current"] == "session_closeout"
    assert closeout["closeout"]["items_reviewed"] == 2
    assert closeout["closeout"]["actions_verified"] == {}
    assert closeout["closeout"]["awaiting_execution"] == 0
    assert closeout["closeout"]["activities_completed"] == ["Email"]
    assert closeout["closeout"]["activities_unavailable"] == ["Objectives"]
    assert finished["status"] == "completed"
