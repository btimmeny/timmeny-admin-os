"""Refreshing the mail is a restart, and nothing else can pretend to be one.

"Refresh mail" answered by `startDailyReview` hands back the finished review
unchanged — no Gmail read, no new mail, nothing to see — and a caller with only
prose to go on reports that as a check. So the finished review carries the
request that actually reviews the day again, as data.
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
    FakeGmailClient,
    begin,
    mailbox,
    thread,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'review-restart.db'}"
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


def post(client: TestClient, path: str, **body: Any) -> dict[str, Any]:
    response = client.post(path, headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def subjects(body: dict[str, Any]) -> list[str]:
    group = body["current_group"]
    return [] if group is None else [item["subject"] for item in group["items"]]


def work_the_day(client: TestClient) -> dict[str, Any]:
    """Decide every row Brian is shown, until the review is finished with."""
    body = begin(client, post(client, "/review/start", sync=True)["run_id"])
    while body["current_group"] is not None:
        item_id = body["current_group"]["items"][0]["item_id"]
        body = client.post(
            f"/review/runs/{body['run_id']}/items/{item_id}/decision",
            headers=AUTH,
            json={"decision": "dismiss"},
        ).json()["run"]
    assert body["status"] == "completed"
    return body


def arrives(fake: FakeGmailClient, item: Any, label_id: str = "Label_4") -> None:
    """Mail that lands after the review was finished with."""
    fake.threads[item.thread_id] = item
    fake.threads_by_label.setdefault(label_id, []).append(item.thread_id)


def test_refreshing_mail_on_a_finished_review_reads_what_arrived_since(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: "check again" must reach Gmail, not the database."""
    fake = mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    finished = work_the_day(client)
    arrives(fake, thread("t3", "Invoice 4021"))

    restarted = post(client, "/review/restart", sync=True)
    begun = begin(client, restarted["run_id"])
    seen = subjects(begun) + subjects(
        client.post(
            f"/review/runs/{begun['run_id']}/items/"
            f"{begun['current_group']['items'][0]['item_id']}/decision",
            headers=AUTH,
            json={"decision": "dismiss"},
        ).json()["run"]
    )

    assert restarted["run_id"] != finished["run_id"]
    assert restarted["revision"] == finished["revision"] + 1
    assert restarted["status"] == "not_started"
    assert "Invoice 4021" in seen


def test_the_finished_review_stays_exactly_what_it_was(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart opens a review; it does not rewrite the one it replaces."""
    fake = mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    finished = work_the_day(client)
    arrives(fake, thread("t3", "Invoice 4021"))

    post(client, "/review/restart", sync=True)

    was = client.get(f"/review/runs/{finished['run_id']}", headers=AUTH).json()
    assert was["summary"] == finished["summary"]
    assert was["completed_at"].rstrip("Z") == finished["completed_at"].rstrip("Z")
    assert [group["capability_key"] for group in was["groups"]] == [
        group["capability_key"] for group in finished["groups"]
    ]
    assert was["status"] == "abandoned"
    """Set aside as the current review, with everything it recorded intact."""


def test_restarting_with_no_new_mail_still_opens_a_fresh_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Check again" is answered by checking, whatever the answer turns out to be.

    Nothing new having arrived is a finding, and it is one only a fresh review
    of refreshed mail can report: the alternative is the old review handed back
    with the day's work on it, described as a check.
    """
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    finished = work_the_day(client)

    restarted = post(client, "/review/restart", sync=True)

    assert restarted["run_id"] != finished["run_id"]
    assert restarted["revision"] == finished["revision"] + 1
    assert restarted["plan"]["opening"]["mode"] == "new"
    assert [group["items"] for group in restarted["plan"]["groups"]] == [0, 0]
    assert restarted["summary"]["reviewed"] == 0


def test_a_finished_review_carries_the_request_that_refreshes_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The restart is data, so acting on it takes no interpretation."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    finished = work_the_day(client)

    again = post(client, "/review/start", sync=False)

    assert again["run_id"] == finished["run_id"]
    assert again["prompt"]["reason"] == "review_completed"
    assert again["restart_available"] is True
    assert again["restart_action"] == {
        "name": "restartDailyReview",
        "method": "POST",
        "path": "/review/restart",
        "body": {"sync": True, "scope": "inbox"},
    }

    fresh = post(client, again["restart_action"]["path"], **again["restart_action"]["body"])
    assert fresh["run_id"] != finished["run_id"]


def test_a_review_under_way_offers_no_restart(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offering to start over on a half-worked morning is offering to lose it."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    body = begin(client, post(client, "/review/start", sync=True)["run_id"])

    assert body["restart_available"] is False
    assert body["restart_action"] is None


def test_continuing_a_review_under_way_opens_no_new_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Continue" is the one word that must never produce a second review."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    body = begin(client, post(client, "/review/start", sync=True)["run_id"])

    resumed = post(client, "/review/continue", sync=False)

    assert resumed["run_id"] == body["run_id"]
    assert resumed["revision"] == 1
    assert resumed["restart_available"] is False
    assert resumed["plan"]["opening"]["mode"] == "resumed"
    """A plan agreed and rows shown is a morning under way, whatever the run
    state says about decisions."""


def test_the_evening_entry_creates_resumes_or_offers_the_restart(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Good evening" is one call with three honest answers."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )

    opened = post(client, "/review/start", sync=True)
    assert opened["status"] == "not_started"
    assert opened["restart_available"] is False

    begun = begin(client, opened["run_id"])
    resumed = post(client, "/review/start", sync=False)
    assert resumed["run_id"] == begun["run_id"]
    assert resumed["restart_available"] is False

    work_the_day(client)
    finished = post(client, "/review/start", sync=False)
    assert finished["status"] == "completed"
    assert finished["restart_available"] is True
    assert finished["restart_action"]["body"] == {"sync": True, "scope": "inbox"}


# A scope prepared in the review being replaced is disarmed by the restart:
# tests/test_adminos_review_lifecycle.py proves it at the domain boundary,
# where the execution refusal lives.
