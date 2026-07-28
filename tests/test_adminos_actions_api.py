from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Sequence

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import main
from adminos.adapters.gmail import GmailDraft, GmailThread
from adminos.api import actions as actions_module
from adminos.api import review as review_module
from adminos.capabilities.config import clear_cache
from adminos.db import engine as engine_module


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPOSITORY_ROOT / "tests/data/capabilities_actions.yaml"
API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}
ADMIN_LABEL_ID = "Label_admin"


class FakeGmail:
    """One mailbox serving both the review's reads and the action's writes."""

    def __init__(self) -> None:
        self.labels = {"Admin": ADMIN_LABEL_ID, "financial/taxes": "Label_taxes"}
        self.thread_labels: dict[str, list[str]] = {}
        self.drafts: dict[str, GmailDraft] = {}
        self.sent: list[str] = []
        self.writes: list[tuple[str, object]] = []
        self.subjects: dict[str, str] = {}

    def serve(self, thread_id: str, subject: str) -> None:
        self.subjects[thread_id] = subject
        self.thread_labels[thread_id] = ["INBOX", ADMIN_LABEL_ID]

    async def resolve_label_id(self, label_name: str) -> str | None:
        return self.labels.get(label_name)

    async def list_thread_ids(self, label_ids: Sequence[str], limit: int) -> list[str]:
        if ADMIN_LABEL_ID not in label_ids:
            return []
        return list(self.subjects)[:limit]

    async def fetch_thread(self, thread_id: str) -> GmailThread:
        return GmailThread(
            thread_id=thread_id,
            message_id="m1",
            subject=self.subjects[thread_id],
            participants=["news@example.com"],
            received_at=datetime(2026, 7, 20, tzinfo=UTC),
            snippet="Nothing to do.",
            label_ids=list(self.thread_labels[thread_id]),
        )

    async def modify_thread(
        self,
        thread_id: str,
        add_label_ids: Sequence[str] = (),
        remove_label_ids: Sequence[str] = (),
    ) -> GmailThread:
        self.writes.append(("modify", (thread_id, list(add_label_ids), list(remove_label_ids))))
        labels = self.thread_labels[thread_id]
        for label_id in add_label_ids:
            if label_id not in labels:
                labels.append(label_id)
        for label_id in remove_label_ids:
            if label_id in labels:
                labels.remove(label_id)
        return await self.fetch_thread(thread_id)

    async def create_draft(
        self,
        thread_id: str,
        to: Sequence[str],
        subject: str,
        body: str,
        cc: Sequence[str] = (),
    ) -> GmailDraft:
        draft = GmailDraft(draft_id="draft-1", message_id="msg-1", thread_id=thread_id)
        self.drafts[draft.draft_id] = draft
        self.writes.append(("draft", (thread_id, subject, body)))
        return draft

    async def fetch_draft(self, draft_id: str) -> GmailDraft | None:
        return self.drafts.get(draft_id)

    async def find_draft_for_thread(self, thread_id: str) -> GmailDraft | None:
        for draft in self.drafts.values():
            if draft.thread_id == thread_id:
                return draft
        return None

    async def send_draft(self, draft_id: str) -> str | None:
        draft = self.drafts.pop(draft_id)
        self.sent.append(draft_id)
        self.writes.append(("send", draft_id))
        return draft.message_id


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    fake = FakeGmail()

    @asynccontextmanager
    async def open_client(_credentials: object) -> AsyncIterator[FakeGmail]:
        yield fake

    monkeypatch.setattr(review_module, "open_gmail_client", open_client)
    monkeypatch.setattr(actions_module, "open_gmail_client", open_client)
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'actions-api.db'}"
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
    monkeypatch.setenv("GMAIL_WRITE_ENABLED", "true")
    clear_cache()
    engine_module.dispose_connection()

    yield TestClient(main.app)

    engine_module.dispose_connection()
    clear_cache()


def approved_run(client: TestClient, gmail: FakeGmail, **decision: Any) -> tuple[str, str]:
    """Start a review, approve the one newsletter, and return run and item ids."""
    gmail.serve("t1", "Newsletter: weekly")
    body = client.post("/review/start", headers=AUTH, json={}).json()
    run_id = body["run_id"]
    item_id = body["current_group"]["items"][0]["item_id"]

    response = client.post(
        f"/review/runs/{run_id}/items/{item_id}/decision",
        headers=AUTH,
        json=decision or {"decision": "approve"},
    )
    assert response.status_code == 200, response.text
    return run_id, item_id


def prepare(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.post(f"/review/runs/{run_id}/actions/prepare", headers=AUTH, json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_action_routes_require_authentication(client: TestClient) -> None:
    assert client.post("/review/runs/any/actions/prepare", json={}).status_code == 401
    assert client.get("/review/runs/any/actions").status_code == 401


def test_preparing_plans_an_approval_without_touching_gmail(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, item_id = approved_run(client, gmail)

    body = prepare(client, run_id)

    action = body["actions"][0]
    assert action["state"] == "prepared"
    assert action["action"] == "gmail.archive"
    assert action["item_id"] == item_id
    assert action["prepared_params"] == {"remove_labels": ["INBOX"], "thread_id": "t1"}
    assert len(action["idempotency_key"]) == 64
    assert gmail.writes == []


def test_preparing_twice_returns_the_same_action(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, _ = approved_run(client, gmail)

    first = prepare(client, run_id)
    second = prepare(client, run_id)

    assert first["actions"][0]["action_id"] == second["actions"][0]["action_id"]
    assert second["counts"]["total"] == 1


def test_executing_needs_to_be_asked_for_explicitly(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, _ = approved_run(client, gmail)
    prepare(client, run_id)

    response = client.post(f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={})

    assert response.status_code == 400
    assert "confirm=true" in response.json()["detail"]
    assert gmail.writes == []


def test_the_kill_switch_refuses_execution(
    client: TestClient, gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, _ = approved_run(client, gmail)
    prepare(client, run_id)
    monkeypatch.setenv("GMAIL_WRITE_ENABLED", "false")

    response = client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )

    assert response.status_code == 409
    assert "GMAIL_WRITE_ENABLED" in response.json()["detail"]
    assert gmail.writes == []


def test_executing_archives_the_thread_and_reads_it_back(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, _ = approved_run(client, gmail)
    prepare(client, run_id)

    response = client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )

    action = response.json()["actions"][0]
    assert response.status_code == 200, response.text
    assert action["state"] == "completed"
    assert action["verification"] == {"labels": [ADMIN_LABEL_ID]}
    assert gmail.writes == [("modify", ("t1", [], ["INBOX"]))]


def test_executing_twice_does_not_write_twice(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, _ = approved_run(client, gmail)
    prepare(client, run_id)
    body = {"confirm": True}

    client.post(f"/review/runs/{run_id}/actions/execute", headers=AUTH, json=body)
    client.post(f"/review/runs/{run_id}/actions/execute", headers=AUTH, json=body)

    assert gmail.writes == [("modify", ("t1", [], ["INBOX"]))]


def test_an_action_carries_its_own_audit_trail(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, _ = approved_run(client, gmail)
    action_id = prepare(client, run_id)["actions"][0]["action_id"]
    client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )

    response = client.get(f"/review/runs/{run_id}/actions/{action_id}", headers=AUTH)

    events = [event["event"] for event in response.json()["events"]]
    assert response.status_code == 200
    assert events == [
        "approved",
        "prepared",
        "execution_started",
        "executed",
        "verified",
    ]


def test_actions_can_be_listed_by_state(client: TestClient, gmail: FakeGmail) -> None:
    run_id, _ = approved_run(client, gmail)
    prepare(client, run_id)

    response = client.get(
        f"/review/runs/{run_id}/actions", headers=AUTH, params={"state": "prepared"}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["counts"] == {"total": 1, "prepared": 1}
    assert body["gmail_write_enabled"] is True


def test_verifying_re_reads_gmail_without_writing(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, _ = approved_run(client, gmail)
    action_id = prepare(client, run_id)["actions"][0]["action_id"]
    client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )

    response = client.post(f"/review/runs/{run_id}/actions/{action_id}/verify", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["state"] == "completed"
    assert len(gmail.writes) == 1


def test_an_unknown_action_is_reported(client: TestClient, gmail: FakeGmail) -> None:
    run_id, _ = approved_run(client, gmail)

    assert client.get(f"/review/runs/{run_id}/actions/missing", headers=AUTH).status_code == 404
    assert (
        client.post(f"/review/runs/{run_id}/actions/missing/retry", headers=AUTH).status_code
        == 404
    )


def test_a_capability_that_may_not_execute_is_refused_with_a_reason(
    client: TestClient, gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, item_id = approved_run(
        client,
        gmail,
        decision="override",
        action="monday.create_task",
    )

    response = client.post(f"/review/runs/{run_id}/actions/prepare", headers=AUTH, json={})

    assert response.status_code == 409
    assert "not permitted to execute" in response.json()["detail"]
    assert item_id
    assert gmail.writes == []


def test_a_draft_is_written_but_not_sent(client: TestClient, gmail: FakeGmail) -> None:
    run_id, _ = approved_run(
        client,
        gmail,
        decision="override",
        action="gmail.draft_reply",
        action_params={"to": ["news@example.com"], "body": "Please unsubscribe me."},
    )
    prepare(client, run_id)

    response = client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )

    action = response.json()["actions"][0]
    assert action["state"] == "completed"
    assert action["verification"]["sent"] is False
    assert gmail.sent == []


def test_sending_a_draft_needs_the_exact_draft_and_a_confirmation(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, item_id = approved_run(
        client,
        gmail,
        decision="override",
        action="gmail.draft_reply",
        action_params={"to": ["news@example.com"], "body": "Please unsubscribe me."},
    )
    prepare(client, run_id)
    client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )
    path = f"/review/runs/{run_id}/items/{item_id}/send-draft"

    unconfirmed = client.post(
        path, headers=AUTH, json={"draft_id": "draft-1", "draft_message_id": "msg-1"}
    )
    wrong_draft = client.post(
        path,
        headers=AUTH,
        json={"draft_id": "draft-1", "draft_message_id": "msg-other", "confirm": True},
    )

    assert unconfirmed.status_code == 400
    assert wrong_draft.status_code == 409
    assert gmail.sent == []


def test_an_approved_send_is_still_only_an_approval(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, item_id = approved_run(
        client,
        gmail,
        decision="override",
        action="gmail.draft_reply",
        action_params={"to": ["news@example.com"], "body": "Please unsubscribe me."},
    )
    prepare(client, run_id)
    client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )

    approval = client.post(
        f"/review/runs/{run_id}/items/{item_id}/send-draft",
        headers=AUTH,
        json={"draft_id": "draft-1", "draft_message_id": "msg-1", "confirm": True},
    )

    assert approval.status_code == 200, approval.text
    assert approval.json()["state"] == "approved"
    assert gmail.sent == []

    sent = client.post(
        f"/review/runs/{run_id}/actions/execute",
        headers=AUTH,
        json={"confirm": True, "action_ids": [approval.json()["action_id"]]},
    )

    assert sent.json()["actions"][0]["state"] == "completed"
    assert gmail.sent == ["draft-1"]


def test_nothing_can_ask_for_a_thread_to_be_deleted(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Trash is not a permitted action anywhere, so the decision is refused."""
    gmail.serve("t1", "Newsletter: weekly")
    body = client.post("/review/start", headers=AUTH, json={}).json()
    item_id = body["current_group"]["items"][0]["item_id"]

    response = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "override", "action": "gmail.trash"},
    )

    assert response.status_code in {409, 422}
    assert gmail.writes == []
