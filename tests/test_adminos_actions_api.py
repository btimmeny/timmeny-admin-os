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
        self.labels = {
            "Admin": ADMIN_LABEL_ID,
            "financial/taxes": "Label_taxes",
            "Later": "Label_later",
            "Notes": "Label_notes",
        }
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

    async def trash_thread(self, thread_id: str) -> GmailThread:
        self.writes.append(("trash", thread_id))
        labels = [label for label in self.thread_labels[thread_id] if label != "INBOX"]
        self.thread_labels[thread_id] = labels + ["TRASH"]
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


def test_an_eligible_row_offers_both_dispositions_by_their_canonical_names(
    client: TestClient, gmail: FakeGmail
) -> None:
    """The row carries the permission, so nothing has to be assumed about it."""
    gmail.serve("t1", "Newsletter: weekly")

    body = client.post("/review/start", headers=AUTH, json={}).json()

    row = body["current_group"]["screen"]["rows"][0]
    assert "archive_gmail_thread" in row["actions"]
    assert "move_gmail_thread_to_trash" in row["actions"]


def test_deleting_one_thread_records_a_trash_override(
    client: TestClient, gmail: FakeGmail
) -> None:
    """\"Delete row 1\" is a Trash override, and it writes nothing by itself."""
    run_id, _ = approved_run(
        client, gmail, decision="override", action="move_gmail_thread_to_trash"
    )

    body = client.get(f"/review/runs/{run_id}", headers=AUTH).json()

    assert body["groups"][0]["counts"]["approved"] == 1
    assert gmail.writes == []


def test_delete_all_of_them_moves_every_thread_to_trash(
    client: TestClient, gmail: FakeGmail
) -> None:
    """\"Delete all 11\": one bulk decision, one confirmation, eleven verified moves."""
    for number in range(1, 12):
        gmail.serve(f"t{number}", f"Newsletter: issue {number}")
    body = client.post("/review/start", headers=AUTH, json={}).json()
    run_id = body["run_id"]

    decided = client.post(
        f"/review/runs/{run_id}/groups/admin/decisions",
        headers=AUTH,
        json={"decision": "override", "action": "move_gmail_thread_to_trash"},
    )

    assert decided.status_code == 200, decided.text
    assert len(decided.json()["decided"]) == 11
    assert {item["approved_action"] for item in decided.json()["decided"]} == {"gmail.trash"}

    prepared = prepare(client, run_id)

    assert {action["action"] for action in prepared["actions"]} == {"gmail.trash"}
    assert gmail.writes == []

    unconfirmed = client.post(f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={})

    assert unconfirmed.status_code == 400
    assert gmail.writes == []

    executed = client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )

    states = [action["state"] for action in executed.json()["actions"]]
    assert states == ["completed"] * 11
    assert sorted(gmail.writes) == sorted(
        ("trash", f"t{number}") for number in range(1, 12)
    )
    assert all("TRASH" in labels for labels in gmail.thread_labels.values())


def test_a_bulk_delete_naming_one_ineligible_row_records_nothing(
    client: TestClient, gmail: FakeGmail
) -> None:
    """One refusal answers for the whole request, and names itself."""
    for number in (1, 2, 3):
        gmail.serve(f"t{number}", f"Newsletter: issue {number}")
    body = client.post("/review/start", headers=AUTH, json={}).json()
    run_id = body["run_id"]
    rows = {row["thread_id"]: row["item_id"] for row in body["current_group"]["screen"]["rows"]}
    client.post(
        f"/review/runs/{run_id}/items/{rows['t2']}/decision",
        headers=AUTH,
        json={"decision": "dismiss"},
    )

    refused = client.post(
        f"/review/runs/{run_id}/groups/admin/decisions",
        headers=AUTH,
        json={
            "decision": "override",
            "action": "move_gmail_thread_to_trash",
            "item_ids": [rows["t1"], rows["t2"], rows["t3"]],
        },
    )

    detail = refused.json()["detail"]
    assert refused.status_code == 409
    assert [entry["thread_id"] for entry in detail["ineligible"]] == ["t2"]
    assert "already dismissed" in detail["ineligible"][0]["reason"]

    unchanged = client.get(f"/review/runs/{run_id}", headers=AUTH).json()
    assert unchanged["groups"][0]["counts"]["pending"] == 2
    assert gmail.writes == []


def test_a_trashed_thread_leaves_the_table_but_not_the_record(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, item_id = approved_run(
        client, gmail, decision="override", action="move_gmail_thread_to_trash"
    )
    prepare(client, run_id)
    client.post(f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True})

    body = client.get(f"/review/runs/{run_id}", headers=AUTH).json()
    history = client.get(f"/review/runs/{run_id}/actions", headers=AUTH).json()

    group = body["groups"][0]
    assert group["counts"] == {"total": 1, "executed": 1}
    assert [action["item_id"] for action in history["actions"]] == [item_id]


def test_running_the_same_trash_again_changes_nothing(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Retrying after a lost response must not trash twice or fail."""
    run_id, _ = approved_run(
        client, gmail, decision="override", action="move_gmail_thread_to_trash"
    )
    prepare(client, run_id)
    first = client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )
    action_id = first.json()["actions"][0]["action_id"]

    again = client.post(
        f"/review/runs/{run_id}/actions/execute",
        headers=AUTH,
        json={"confirm": True, "action_ids": [action_id]},
    )

    assert again.status_code == 200, again.text
    assert gmail.writes == [("trash", "t1")]


def test_a_row_offers_filing_with_the_folders_it_would_accept(
    client: TestClient, gmail: FakeGmail
) -> None:
    """The folder list comes from Admin OS, so the GPT never invents one."""
    gmail.serve("t1", "Newsletter: weekly")

    body = client.post("/review/start", headers=AUTH, json={}).json()

    screen = body["current_group"]["screen"]
    filing = next(
        action for action in screen["actions"] if action["id"] == "move_gmail_thread_to_label"
    )
    assert "move_gmail_thread_to_label" in screen["rows"][0]["actions"]
    assert filing["params"] == [
        {"name": "label", "label": "Folder", "required": True, "choices": ["Later", "Notes"]}
    ]


def test_filing_one_thread_files_it_and_takes_it_out_of_the_inbox(
    client: TestClient, gmail: FakeGmail
) -> None:
    """\"Keep it, but move it to Later\": one write, verified as both halves."""
    run_id, item_id = approved_run(
        client,
        gmail,
        decision="override",
        action="move_gmail_thread_to_label",
        action_params={"label": "Later"},
    )

    prepared = prepare(client, run_id)

    assert prepared["actions"][0]["action"] == "gmail.move"
    assert prepared["actions"][0]["prepared_params"] == {
        "thread_id": "t1",
        "label": "Later",
        "add_labels": ["Later"],
        "remove_labels": ["INBOX"],
    }
    assert gmail.writes == []

    unconfirmed = client.post(f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={})

    assert unconfirmed.status_code == 400
    assert gmail.writes == []

    executed = client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )
    action = executed.json()["actions"][0]

    assert action["state"] == "completed"
    assert action["item_id"] == item_id
    assert gmail.writes == [("modify", ("t1", ["Label_later"], ["INBOX"]))]
    assert gmail.thread_labels["t1"] == [ADMIN_LABEL_ID, "Label_later"]


def test_filing_all_of_them_puts_every_thread_in_the_named_folder(
    client: TestClient, gmail: FakeGmail
) -> None:
    for number in (1, 2, 3):
        gmail.serve(f"t{number}", f"Newsletter: issue {number}")
    run_id = client.post("/review/start", headers=AUTH, json={}).json()["run_id"]

    decided = client.post(
        f"/review/runs/{run_id}/groups/admin/decisions",
        headers=AUTH,
        json={
            "decision": "override",
            "action": "move_gmail_thread_to_label",
            "action_params": {"label": "Notes"},
        },
    )

    assert decided.status_code == 200, decided.text
    assert {item["approved_action"] for item in decided.json()["decided"]} == {"gmail.move"}

    prepare(client, run_id)
    executed = client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json={"confirm": True}
    )

    assert [action["state"] for action in executed.json()["actions"]] == ["completed"] * 3
    assert sorted(gmail.writes) == sorted(
        ("modify", (f"t{number}", ["Label_notes"], ["INBOX"])) for number in (1, 2, 3)
    )


def test_filing_without_naming_a_folder_is_refused(
    client: TestClient, gmail: FakeGmail
) -> None:
    gmail.serve("t1", "Newsletter: weekly")
    body = client.post("/review/start", headers=AUTH, json={}).json()
    item_id = body["current_group"]["items"][0]["item_id"]

    refused = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "override", "action": "move_gmail_thread_to_label"},
    )

    assert refused.status_code == 409
    assert "must name the folder" in refused.json()["detail"]
    assert gmail.writes == []


def test_a_folder_outside_the_capabilitys_list_is_refused(
    client: TestClient, gmail: FakeGmail
) -> None:
    gmail.serve("t1", "Newsletter: weekly")
    body = client.post("/review/start", headers=AUTH, json={}).json()
    item_id = body["current_group"]["items"][0]["item_id"]

    refused = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={
            "decision": "override",
            "action": "move_gmail_thread_to_label",
            "action_params": {"label": "Somewhere Else"},
        },
    )

    assert refused.status_code == 409
    assert "does not file mail in 'Somewhere Else'" in refused.json()["detail"]
    assert gmail.writes == []


def test_nothing_can_ask_for_a_thread_to_be_destroyed(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Trash is as far as it goes: permanent deletion is not a nameable action."""
    gmail.serve("t1", "Newsletter: weekly")
    body = client.post("/review/start", headers=AUTH, json={}).json()
    item_id = body["current_group"]["items"][0]["item_id"]

    for action in ("gmail.delete", "delete_gmail_thread", "gmail.destroy"):
        response = client.post(
            f"/review/runs/{body['run_id']}/items/{item_id}/decision",
            headers=AUTH,
            json={"decision": "override", "action": action},
        )
        assert response.status_code == 422, response.text

    assert gmail.writes == []
