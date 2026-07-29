from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Sequence

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import main
from adminos.adapters.gmail import GmailThread
from adminos.api import review as review_module
from adminos.capabilities.config import clear_cache
from adminos.db import engine as engine_module


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPOSITORY_ROOT / "tests/data/capabilities_actions.yaml"
API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}


def opened(client: TestClient, **body: Any) -> dict[str, Any]:
    """Start today's review and begin its plan, which is where rows appear.

    A review states its plan before it presents anything, so a test that wants
    a table needs the two calls a morning needs.
    """
    started = client.post("/review/start", headers=AUTH, json=body)
    assert started.status_code == 200, started.text
    return begin(client, started.json()["run_id"])


def begin(client: TestClient, run_id: str, **plan: Any) -> dict[str, Any]:
    response = client.post(f"/review/runs/{run_id}/plan", headers=AUTH, json=plan)
    assert response.status_code == 200, response.text
    return response.json()
ADMIN_LABEL_ID = "Label_admin"
SENDER = "billing@utility.example"
RULE = {
    "capability_key": "admin",
    "match": {"participant_domains": ["utility.example"]},
    "action": "gmail.archive",
    "rationale": "Utility receipts are filed, not answered.",
}


class FakeGmail:
    def __init__(self, subjects: dict[str, str]) -> None:
        self.subjects = subjects

    async def resolve_label_id(self, label_name: str) -> str | None:
        return {"Admin": ADMIN_LABEL_ID}.get(label_name)

    async def list_thread_ids(
        self,
        label_ids: Sequence[str],
        limit: int,
        query: str | None = None,
    ) -> list[str]:
        if not {"INBOX", ADMIN_LABEL_ID} >= set(label_ids):
            return []
        return list(self.subjects)[:limit]

    async def fetch_thread(self, thread_id: str) -> GmailThread:
        return GmailThread(
            thread_id=thread_id,
            message_id="m1",
            subject=self.subjects[thread_id],
            participants=[SENDER],
            received_at=datetime(2026, 7, 20, tzinfo=UTC),
            snippet="Your bill is ready.",
            label_ids=["INBOX", ADMIN_LABEL_ID],
        )


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    fake = FakeGmail({"t1": "Your July statement"})

    @asynccontextmanager
    async def open_client(_credentials: object) -> AsyncIterator[FakeGmail]:
        yield fake

    monkeypatch.setattr(review_module, "open_gmail_client", open_client)
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'learning-api.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("CAPABILITIES_PATH", str(CONFIG_PATH))
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("GMAIL_WRITE_ENABLED", "false")
    clear_cache()
    engine_module.dispose_connection()

    yield TestClient(main.app)

    engine_module.dispose_connection()
    clear_cache()


def override(client: TestClient) -> dict[str, Any]:
    body = opened(client)
    item_id = body["current_group"]["items"][0]["item_id"]
    response = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "override", "action": "gmail.archive", "note": "Always file these."},
    )
    assert response.status_code == 200, response.text
    return body


def propose(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post("/learning/rules", headers=AUTH, json={**RULE, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def test_the_learning_routes_require_authentication(client: TestClient) -> None:
    assert client.get("/learning/events").status_code == 401
    assert client.get("/learning/rules").status_code == 401
    assert client.post("/learning/rules", json=RULE).status_code == 401


def test_a_correction_is_visible_as_a_learning_event(
    client: TestClient, mailbox: FakeGmail
) -> None:
    override(client)

    response = client.get("/learning/events", headers=AUTH)

    event = response.json()["events"][0]
    assert response.status_code == 200
    assert event["kind"] == "override"
    assert event["chosen"] == "gmail.archive"
    assert event["capability_key"] == "admin"
    assert event["signals"]["participant_domains"] == ["utility.example"]


def test_a_correction_does_not_become_a_rule(
    client: TestClient, mailbox: FakeGmail
) -> None:
    """The whole point: learning is evidence until somebody says otherwise."""
    override(client)

    rules = client.get("/learning/rules", headers=AUTH).json()["rules"]

    assert [rule["state"] for rule in rules] == ["observed"]
    assert rules[0]["active"] is False
    assert rules[0]["may_execute_without_approval"] is False


def test_events_can_be_filtered_by_capability(
    client: TestClient, mailbox: FakeGmail
) -> None:
    override(client)

    response = client.get(
        "/learning/events", headers=AUTH, params={"capability_key": "financial_taxes"}
    )

    assert response.json()["events"] == []


def test_a_proposed_rule_states_exactly_what_it_would_do(client: TestClient) -> None:
    rule = propose(client)

    assert rule["state"] == "proposed"
    assert rule["match"] == {"participant_domains": ["utility.example"]}
    assert rule["action"] == "gmail.archive"
    assert rule["active"] is False
    assert rule["next_states"] == ["confirmed", "retired"]


def test_a_rule_that_matches_everything_is_refused(client: TestClient) -> None:
    response = client.post("/learning/rules", headers=AUTH, json={**RULE, "match": {}})

    assert response.status_code == 422


def test_a_rule_may_not_ask_for_an_action_the_capability_lacks(client: TestClient) -> None:
    response = client.post(
        "/learning/rules",
        headers=AUTH,
        json={**RULE, "capability_key": "financial_taxes", "action": "gmail.archive"},
    )

    assert response.status_code == 409
    assert "not allowed" in response.json()["detail"]


def test_an_unknown_capability_is_reported(client: TestClient) -> None:
    response = client.post(
        "/learning/rules", headers=AUTH, json={**RULE, "capability_key": "nope"}
    )

    assert response.status_code == 404


def test_confirming_activates_a_rule_but_does_not_automate_it(client: TestClient) -> None:
    rule_id = propose(client)["rule_id"]

    response = client.post(f"/learning/rules/{rule_id}/confirm", headers=AUTH, json={})

    body = response.json()
    assert response.status_code == 200
    assert body["state"] == "confirmed"
    assert body["active"] is True
    assert body["may_execute_without_approval"] is False


def test_promotion_has_to_be_asked_for_in_so_many_words(client: TestClient) -> None:
    rule_id = propose(client)["rule_id"]
    client.post(f"/learning/rules/{rule_id}/confirm", headers=AUTH, json={})

    response = client.post(f"/learning/rules/{rule_id}/promote", headers=AUTH, json={})

    assert response.status_code == 400
    assert "without approval" in response.json()["detail"]


def test_a_promoted_rule_is_the_only_kind_that_may_act_unasked(client: TestClient) -> None:
    rule_id = propose(client)["rule_id"]
    client.post(f"/learning/rules/{rule_id}/confirm", headers=AUTH, json={})

    response = client.post(
        f"/learning/rules/{rule_id}/promote", headers=AUTH, json={"confirm": True}
    )

    body = response.json()
    assert body["state"] == "automatable"
    assert body["may_execute_without_approval"] is True


def test_a_rule_cannot_be_promoted_before_it_is_confirmed(client: TestClient) -> None:
    rule_id = propose(client)["rule_id"]

    response = client.post(
        f"/learning/rules/{rule_id}/promote", headers=AUTH, json={"confirm": True}
    )

    assert response.status_code == 409
    assert "cannot become" in response.json()["detail"]


def test_a_confirmed_rule_recommends_on_the_next_review(
    client: TestClient, mailbox: FakeGmail
) -> None:
    rule_id = propose(client)["rule_id"]
    client.post(f"/learning/rules/{rule_id}/confirm", headers=AUTH, json={})

    item = opened(client)["current_group"]["items"][0]

    assert item["recommendation"] == "gmail.archive"
    assert item["recommendation_source"] == "learned_rule"
    assert item["state"] == "pending"


def test_a_promoted_rule_approves_without_being_asked(
    client: TestClient, mailbox: FakeGmail
) -> None:
    rule_id = propose(client)["rule_id"]
    client.post(f"/learning/rules/{rule_id}/confirm", headers=AUTH, json={})
    client.post(f"/learning/rules/{rule_id}/promote", headers=AUTH, json={"confirm": True})

    item = opened(client)["current_group"]["items"][0]

    assert item["state"] == "approved"
    assert item["approved_action"] == "gmail.archive"


def test_retiring_a_rule_stops_it_recommending(
    client: TestClient, mailbox: FakeGmail
) -> None:
    rule_id = propose(client)["rule_id"]
    client.post(f"/learning/rules/{rule_id}/confirm", headers=AUTH, json={})

    retired = client.post(
        f"/learning/rules/{rule_id}/retire",
        headers=AUTH,
        json={"reason": "The account is closed."},
    )
    item = opened(client)["current_group"]["items"][0]

    assert retired.json()["state"] == "retired"
    assert retired.json()["retired_reason"] == "The account is closed."
    assert item["recommendation_source"] != "learned_rule"


def test_a_rule_that_files_mail_names_the_folder_it_files_into(
    client: TestClient, mailbox: FakeGmail
) -> None:
    """A learned move is only reviewable if the reader can see where it files."""
    rule_id = propose(
        client,
        action="gmail.move",
        action_params={"label": "Later"},
        rationale="Utility receipts are kept, out of the inbox.",
    )["rule_id"]
    client.post(f"/learning/rules/{rule_id}/confirm", headers=AUTH, json={})

    rule = client.get(f"/learning/rules/{rule_id}", headers=AUTH).json()
    item = opened(client)["current_group"]["items"][0]

    assert rule["action_params"] == {"label": "Later"}
    assert item["recommendation"] == "gmail.move"
    assert item["recommendation_params"] == {"label": "Later"}


def test_a_rule_may_not_file_mail_in_a_folder_the_capability_lacks(client: TestClient) -> None:
    response = client.post(
        "/learning/rules",
        headers=AUTH,
        json={**RULE, "action": "gmail.move", "action_params": {"label": "Career/Citi"}},
    )

    assert response.status_code == 409
    assert "not one of" in response.json()["detail"]


def test_a_rule_that_files_mail_nowhere_is_refused(client: TestClient) -> None:
    """A move with no folder is not a rule anyone could confirm."""
    response = client.post("/learning/rules", headers=AUTH, json={**RULE, "action": "gmail.move"})

    assert response.status_code == 409
    assert "None" in response.json()["detail"]


def test_one_rule_can_be_read_in_full(client: TestClient) -> None:
    rule_id = propose(client)["rule_id"]

    response = client.get(f"/learning/rules/{rule_id}", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["rationale"] == RULE["rationale"]


def test_an_unknown_rule_is_reported(client: TestClient) -> None:
    assert client.get("/learning/rules/missing", headers=AUTH).status_code == 404
    assert (
        client.post("/learning/rules/missing/confirm", headers=AUTH, json={}).status_code == 404
    )
