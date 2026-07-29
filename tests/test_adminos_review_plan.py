"""A review states what it is going to do before it does any of it.

The plan is not decoration. A morning that opens with the first table has
agreed its own shape on Brian's behalf: which groups, in what order, how many
rows, and what will not happen merely because he approved something. These
tests hold the review to saying that first, and to counting what it did from
execution rather than from decisions.
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
    begin,
    mailbox,
    proposed,
    start,
    thread,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'review-plan.db'}"
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


def plan_of(body: dict[str, Any]) -> dict[str, Any]:
    return body["plan"]


def test_a_new_review_states_its_plan_and_shows_no_group(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan is the operating contract, and it comes before the first row."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal"), thread("t3", "Parking permit")],
    )

    body = proposed(client)
    plan = plan_of(body)

    assert body["current_group"] is None
    assert body["screen_id"] is None
    assert plan["status"] == "proposed"
    assert plan["group_count"] == 2
    assert plan["items"] == 3
    assert [group["capability_key"] for group in plan["groups"]] == [
        "financial_taxes",
        "admin",
    ]
    assert [group["items"] for group in plan["groups"]] == [1, 2]
    assert body["prompt"]["reason"] == "plan_proposed"
    assert "beginReviewPlan" in {
        choice["operation"] for choice in body["prompt"]["choices"]
    }


def test_the_plan_names_the_mailboxes_it_is_not_reviewing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Did you look at my archive?" is answered from the plan, not inferred."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    plan = plan_of(proposed(client))

    assert plan["excluded"] == [
        "archived",
        "snoozed",
        "Trash",
        "Spam",
        "Sent-only threads",
        "Drafts",
    ]
    assert "Nothing will be archived" in plan["message"]
    assert plan["steps"][0] == "Show every item in the group."
    assert plan["steps"][-1] == "Read Gmail back, and only then report it done."


def test_an_empty_group_is_in_the_plan_and_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A group with nothing in it is part of the morning, and worth stating."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    plan = plan_of(proposed(client))

    assert plan["empty_groups"] == ["Admin"]
    assert [group["empty"] for group in plan["groups"]] == [False, True]


def test_beginning_the_plan_presents_the_first_group(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    run_id = proposed(client)["run_id"]

    body = begin(client, run_id)

    assert body["plan"]["status"] == "active"
    assert body["current_group"]["capability_key"] == "financial_taxes"
    assert body["plan"]["group_number"] == 1
    assert body["plan"]["group_count"] == 2
    assert body["prompt"] is None


def test_a_named_group_can_be_worked_first(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Do Admin first" reorders this review and nothing else."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    run_id = proposed(client)["run_id"]

    body = begin(client, run_id, order=["admin"])

    assert body["current_group"]["capability_key"] == "admin"
    assert body["plan"]["working"] == ["admin", "financial_taxes"]
    assert body["plan"]["skipped"] == []


def test_only_one_group_sets_the_rest_aside(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A group set aside is not the next group, and is not counted as left."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    run_id = proposed(client)["run_id"]

    body = begin(client, run_id, only=["admin"])
    plan = body["plan"]

    assert plan["working"] == ["admin"]
    assert plan["skipped"] == ["financial_taxes"]
    assert plan["group_count"] == 1
    assert plan["remaining"] == ["Admin"]
    assert body["current_group"]["capability_key"] == "admin"
    assert [group["skipped"] for group in plan["groups"]] == [False, True]


def test_a_review_can_finish_with_a_group_set_aside(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping is a decision about today, so today can still be finished."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    run_id = proposed(client)["run_id"]
    body = begin(client, run_id, only=["admin"])
    item_id = body["current_group"]["items"][0]["item_id"]

    decided = client.post(
        f"/review/runs/{run_id}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "dismiss"},
    )

    assert decided.status_code == 200, decided.text
    assert decided.json()["run"]["status"] == "completed"


def test_a_group_that_is_not_in_the_review_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legal is not a capability, and inventing it would be worse than refusing."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    run_id = proposed(client)["run_id"]

    response = client.post(
        f"/review/runs/{run_id}/plan", headers=AUTH, json={"skip": ["legal"]}
    )

    assert response.status_code == 422
    assert "legal" in response.json()["detail"]
    assert client.get(f"/review/runs/{run_id}", headers=AUTH).json()["plan"][
        "status"
    ] == "proposed"


def test_setting_every_group_aside_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    run_id = proposed(client)["run_id"]

    response = client.post(
        f"/review/runs/{run_id}/plan",
        headers=AUTH,
        json={"skip": ["financial_taxes", "admin"]},
    )

    assert response.status_code == 422
    assert "nothing to work" in response.json()["detail"]


def test_changing_the_order_is_a_new_version_of_the_plan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    run_id = proposed(client)["run_id"]

    first = begin(client, run_id)
    second = begin(client, run_id, order=["admin"])

    assert first["plan"]["version"] == 1
    assert second["plan"]["version"] == 2


def test_deciding_a_row_begins_the_plan_without_being_asked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Working the review is a stronger answer than agreeing to work it."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    body = proposed(client)
    run_id = body["run_id"]
    group = client.get(
        f"/review/runs/{run_id}/groups/financial_taxes", headers=AUTH
    ).json()
    item_id = group["items"][0]["item_id"]

    decided = client.post(
        f"/review/runs/{run_id}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "dismiss"},
    )

    assert decided.status_code == 200, decided.text
    assert decided.json()["run"]["plan"]["status"] == "active"


def test_resuming_says_where_the_review_stands_and_what_it_owes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resumed review reports the groups done, the one in hand, and the debt."""
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Domain renewal")],
    )
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]
    client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "approve"},
    )

    resumed = client.post("/review/continue", headers=AUTH, json={"sync": False}).json()
    plan = resumed["plan"]

    assert plan["status"] == "active"
    assert plan["resumed"] is True
    assert plan["current"] == "financial_taxes"
    assert plan["standing"]["decided_not_executed"] == 1
    assert "not carried out" in plan["message"]
    assert resumed["current_group"]["standing"].startswith(
        "financial/taxes has 1 decision recorded and not carried out"
    )


def test_an_undecided_group_says_how_many_rows_still_need_him(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities"), thread("t2", "IRS notice")],
    )

    body = start(client)

    assert body["current_group"]["standing"] == "financial/taxes has 2 items still needing you."
    assert body["plan"]["groups"][0]["remaining"] == 2


def test_the_summary_counts_nothing_a_decision_did(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approving is not archiving, and the end-of-review counts know it."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    decided = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "approve"},
    )
    summary = decided.json()["run"]["summary"]

    assert summary["done"] == {}
    assert summary["standing"]["decided_not_executed"] == 1
    assert "Counted from verified execution" in summary["message"]
    assert "not finished" in summary["message"]


def test_a_review_of_nothing_is_planned_and_finished(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty morning still says what it looked at before saying it is done."""
    mailbox(monkeypatch)

    body = proposed(client)

    assert body["plan"]["items"] == 0
    assert body["plan"]["empty_groups"] == ["financial/taxes", "Admin"]
    assert body["summary"]["reviewed"] == 0
    assert body["summary"]["standing"]["decided_not_executed"] == 0


def test_an_abandoned_review_takes_no_plan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review set aside is read, not worked, and that includes its plan."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    first = proposed(client)
    client.post("/review/restart", headers=AUTH, json={})

    response = client.post(
        f"/review/runs/{first['run_id']}/plan", headers=AUTH, json={}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "ReviewAbandoned"
