import json
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
        self.queries: list[str | None] = []

    def serve(self, thread_id: str, subject: str) -> None:
        self.subjects[thread_id] = subject
        self.thread_labels[thread_id] = ["INBOX", ADMIN_LABEL_ID]

    async def resolve_label_id(self, label_name: str) -> str | None:
        return self.labels.get(label_name)

    async def list_thread_ids(
        self,
        label_ids: Sequence[str],
        limit: int,
        query: str | None = None,
    ) -> list[str]:
        self.queries.append(query)
        wanted = [
            thread_id
            for thread_id in self.subjects
            if set(label_ids) <= set(self.thread_labels[thread_id])
        ]
        return wanted[:limit]

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

    async def untrash_thread(self, thread_id: str) -> GmailThread:
        self.writes.append(("untrash", thread_id))
        labels = [label for label in self.thread_labels[thread_id] if label != "TRASH"]
        self.thread_labels[thread_id] = labels + ["INBOX"]
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
    body = opened(client)
    run_id = body["run_id"]
    item_id = body["current_group"]["items"][0]["item_id"]

    response = client.post(
        f"/review/runs/{run_id}/items/{item_id}/decision",
        headers=AUTH,
        json=decision or {"decision": "approve"},
    )
    assert response.status_code == 200, response.text
    return run_id, item_id


def prepare(
    client: TestClient,
    run_id: str,
    item_ids: Sequence[str] | None = None,
    capability_key: str = "admin",
) -> dict[str, Any]:
    """Prepare a selection, or say explicitly that the whole capability was asked for."""
    payload: dict[str, Any] = {"capability_key": capability_key}
    if item_ids is None:
        payload["entire_capability"] = True
    else:
        payload["item_ids"] = list(item_ids)
    response = client.post(
        f"/review/runs/{run_id}/actions/prepare", headers=AUTH, json=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


def execute(
    client: TestClient,
    run_id: str,
    prepared: dict[str, Any],
    **extra: Any,
) -> Any:
    """Confirm exactly what a preparation returned, unless a test says otherwise.

    Execution requires the scope restated in full, so the honest default is the
    preparation's own answer; a test that wants a disagreement passes it.
    """
    payload: dict[str, Any] = {
        "scope_id": prepared["scope_id"],
        "item_ids": prepared["prepared_item_ids"],
        "action_ids": prepared["action_ids"],
        "confirm": True,
    }
    payload.update(extra)
    return client.post(
        f"/review/runs/{run_id}/actions/execute", headers=AUTH, json=payload
    )


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
    prepared = prepare(client, run_id)

    response = execute(client, run_id, prepared, confirm=False)

    assert response.status_code == 400
    assert "confirm=true" in response.json()["detail"]
    assert gmail.writes == []


def test_executing_needs_the_scope_restated(
    client: TestClient, gmail: FakeGmail
) -> None:
    """A caller that cannot say what it is running has not read the preparation.

    The restatement is the check: it is what turns "nineteen rows" from
    something the caller believes into something the server agrees with. Left
    optional, the one request that writes to Gmail could be sent by a client
    that never looked at what it was confirming.
    """
    run_id, _ = approved_run(client, gmail)
    prepared = prepare(client, run_id)
    path = f"/review/runs/{run_id}/actions/execute"

    without_items = client.post(
        path,
        headers=AUTH,
        json={
            "scope_id": prepared["scope_id"],
            "action_ids": prepared["action_ids"],
            "confirm": True,
        },
    )
    without_actions = client.post(
        path,
        headers=AUTH,
        json={
            "scope_id": prepared["scope_id"],
            "item_ids": prepared["prepared_item_ids"],
            "confirm": True,
        },
    )
    without_scope = client.post(
        path,
        headers=AUTH,
        json={
            "item_ids": prepared["prepared_item_ids"],
            "action_ids": prepared["action_ids"],
            "confirm": True,
        },
    )

    assert without_items.status_code == 422
    assert without_actions.status_code == 422
    assert without_scope.status_code == 422
    assert gmail.writes == []


def test_the_exact_prepared_scope_executes(client: TestClient, gmail: FakeGmail) -> None:
    """What preparation returns is what execution accepts, field for field."""
    run_id, item_id = approved_run(client, gmail)
    prepared = prepare(client, run_id)

    assert prepared["scope_id"]
    assert prepared["prepared_item_ids"] == [item_id]
    assert prepared["action_ids"] == [prepared["actions"][0]["action_id"]]

    executed = client.post(
        f"/review/runs/{run_id}/actions/execute",
        headers=AUTH,
        json={
            "scope_id": prepared["scope_id"],
            "item_ids": prepared["prepared_item_ids"],
            "action_ids": prepared["action_ids"],
            "confirm": True,
        },
    )

    action = executed.json()["actions"][0]
    assert executed.status_code == 200, executed.text
    assert action["state"] == "completed"
    assert action["action_id"] == prepared["action_ids"][0]
    assert gmail.writes == [("modify", ("t1", [], ["INBOX"]))]


def test_the_kill_switch_refuses_execution(
    client: TestClient, gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, _ = approved_run(client, gmail)
    prepared = prepare(client, run_id)
    monkeypatch.setenv("GMAIL_WRITE_ENABLED", "false")

    response = execute(client, run_id, prepared)

    assert response.status_code == 409
    assert "GMAIL_WRITE_ENABLED" in response.json()["detail"]
    assert gmail.writes == []


def test_executing_archives_the_thread_and_reads_it_back(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, _ = approved_run(client, gmail)
    prepared = prepare(client, run_id)

    response = execute(client, run_id, prepared)

    action = response.json()["actions"][0]
    assert response.status_code == 200, response.text
    assert action["state"] == "completed"
    assert action["verification"] == {"labels": [ADMIN_LABEL_ID]}
    assert gmail.writes == [("modify", ("t1", [], ["INBOX"]))]


def test_executing_twice_does_not_write_twice(
    client: TestClient, gmail: FakeGmail
) -> None:
    """A scope runs once; running it again is refused rather than repeated."""
    run_id, _ = approved_run(client, gmail)
    prepared = prepare(client, run_id)

    first = execute(client, run_id, prepared)
    again = execute(client, run_id, prepared)

    assert first.status_code == 200, first.text
    assert again.status_code == 409
    assert again.json()["detail"]["error"] == "ScopeMismatch"
    assert gmail.writes == [("modify", ("t1", [], ["INBOX"]))]


def test_an_action_carries_its_own_audit_trail(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, _ = approved_run(client, gmail)
    prepared = prepare(client, run_id)
    action_id = prepared["actions"][0]["action_id"]
    execute(client, run_id, prepared)

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
    prepared = prepare(client, run_id)
    action_id = prepared["actions"][0]["action_id"]
    execute(client, run_id, prepared)

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

    response = client.post(
        f"/review/runs/{run_id}/actions/prepare",
        headers=AUTH,
        json={"capability_key": "admin", "item_ids": [item_id]},
    )

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
    prepared = prepare(client, run_id)

    response = execute(client, run_id, prepared)

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
    execute(client, run_id, prepare(client, run_id))
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
    execute(client, run_id, prepare(client, run_id))

    approval = client.post(
        f"/review/runs/{run_id}/items/{item_id}/send-draft",
        headers=AUTH,
        json={"draft_id": "draft-1", "draft_message_id": "msg-1", "confirm": True},
    )

    assert approval.status_code == 200, approval.text
    assert approval.json()["state"] == "approved"
    assert gmail.sent == []

    prepared = prepare(client, run_id, [item_id])
    sent = execute(
        client,
        run_id,
        prepared,
        action_ids=[approval.json()["action_id"]],
    )

    assert prepared["action_ids"] == [approval.json()["action_id"]]
    assert sent.json()["actions"][0]["state"] == "completed"
    assert gmail.sent == ["draft-1"]


def test_an_eligible_row_offers_both_dispositions_by_their_canonical_names(
    client: TestClient, gmail: FakeGmail
) -> None:
    """The row carries the permission, so nothing has to be assumed about it."""
    gmail.serve("t1", "Newsletter: weekly")

    body = opened(client)

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
    body = opened(client)
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
    assert len(prepared["prepared_item_ids"]) == 11
    assert prepared["excluded_items"] == []
    assert prepared["scope_matches_request"] is True
    assert gmail.writes == []

    unconfirmed = execute(client, run_id, prepared, confirm=False)

    assert unconfirmed.status_code == 400
    assert gmail.writes == []

    executed = execute(client, run_id, prepared)

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
    body = opened(client)
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
    execute(client, run_id, prepare(client, run_id))

    body = client.get(f"/review/runs/{run_id}", headers=AUTH).json()
    history = client.get(f"/review/runs/{run_id}/actions", headers=AUTH).json()

    group = body["groups"][0]
    assert group["counts"] == {"total": 1, "executed": 1, "remaining": 0}
    assert [action["item_id"] for action in history["actions"]] == [item_id]


def test_running_the_same_trash_again_changes_nothing(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Retrying after a lost response must not trash twice or fail."""
    run_id, item_id = approved_run(
        client, gmail, decision="override", action="move_gmail_thread_to_trash"
    )
    first = execute(client, run_id, prepare(client, run_id))
    action_id = first.json()["actions"][0]["action_id"]

    again = client.post(
        f"/review/runs/{run_id}/actions/{action_id}/retry", headers=AUTH
    )

    assert again.status_code == 200, again.text
    assert again.json()["item_id"] == item_id
    assert gmail.writes == [("trash", "t1")]


def test_a_row_offers_filing_with_the_folders_it_would_accept(
    client: TestClient, gmail: FakeGmail
) -> None:
    """The folder list comes from Admin OS, so the GPT never invents one."""
    gmail.serve("t1", "Newsletter: weekly")

    body = opened(client)

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

    unconfirmed = execute(client, run_id, prepared, confirm=False)

    assert unconfirmed.status_code == 400
    assert gmail.writes == []

    executed = execute(client, run_id, prepared)
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
    run_id = opened(client)["run_id"]

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

    executed = execute(client, run_id, prepare(client, run_id))

    assert [action["state"] for action in executed.json()["actions"]] == ["completed"] * 3
    assert sorted(gmail.writes) == sorted(
        ("modify", (f"t{number}", ["Label_notes"], ["INBOX"])) for number in (1, 2, 3)
    )


def test_filing_without_naming_a_folder_is_refused(
    client: TestClient, gmail: FakeGmail
) -> None:
    gmail.serve("t1", "Newsletter: weekly")
    body = opened(client)
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
    body = opened(client)
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
    body = opened(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    for action in ("gmail.delete", "delete_gmail_thread", "gmail.destroy"):
        response = client.post(
            f"/review/runs/{body['run_id']}/items/{item_id}/decision",
            headers=AUTH,
            json={"decision": "override", "action": action},
        )
        assert response.status_code == 422, response.text

    assert gmail.writes == []


def start_with_threads(
    client: TestClient, gmail: FakeGmail, count: int
) -> tuple[str, list[dict[str, Any]]]:
    """A review of `count` Admin threads, and its rows in the order shown."""
    for number in range(1, count + 1):
        gmail.serve(f"t{number}", f"Newsletter: issue {number}")
    body = opened(client)
    return body["run_id"], body["current_group"]["screen"]["rows"]


def approve_trash(
    client: TestClient, run_id: str, rows: Sequence[dict[str, Any]]
) -> list[str]:
    """Approve Trash on exactly these rows, and return their item ids."""
    item_ids = [row["item_id"] for row in rows]
    decided = client.post(
        f"/review/runs/{run_id}/groups/admin/decisions",
        headers=AUTH,
        json={
            "decision": "override",
            "action": "move_gmail_thread_to_trash",
            "item_ids": item_ids,
        },
    )
    assert decided.status_code == 200, decided.text
    return item_ids


def group_of(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/review/runs/{run_id}/groups/admin", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def decide(client: TestClient, run_id: str, item_id: str, decision: str) -> None:
    response = client.post(
        f"/review/runs/{run_id}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": decision},
    )
    assert response.status_code == 200, response.text


def settle_all_but(
    client: TestClient, gmail: FakeGmail, count: int, keep: int
) -> tuple[str, list[dict[str, Any]]]:
    """A group that loaded `count` threads and has `keep` rows left to answer."""
    run_id, rows = start_with_threads(client, gmail, count)
    settled = rows[keep:]
    approve_trash(client, run_id, settled)
    prepared = prepare(client, run_id, [row["item_id"] for row in settled])
    executed = execute(client, run_id, prepared)
    assert executed.status_code == 200, executed.text
    return run_id, rows


def test_progress_counts_the_rows_still_open_not_the_ones_loaded_this_morning(
    client: TestClient, gmail: FakeGmail
) -> None:
    """The bug: four rows on the table, described as "4 of 28 still need you"."""
    run_id, _ = settle_all_but(client, gmail, 28, keep=4)

    group = group_of(client, run_id)
    screen = group["screen"]

    assert len(screen["rows"]) == 4
    assert screen["footer"] == "4 items still need you."
    assert "28" not in screen["footer"]
    assert group["counts"]["remaining"] == 4
    assert group["counts"]["total"] == 28


def test_one_more_decision_leaves_three_rows_and_says_three(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, rows = settle_all_but(client, gmail, 28, keep=4)

    decide(client, run_id, rows[0]["item_id"], "dismiss")

    group = group_of(client, run_id)

    assert len(group["screen"]["rows"]) == 3
    assert group["screen"]["footer"] == "3 items still need you."
    assert group["counts"]["remaining"] == 3


def test_the_rows_the_footer_and_the_progress_count_are_the_same_set(
    client: TestClient, gmail: FakeGmail
) -> None:
    """They are read from one list, so they cannot describe different ones."""
    run_id, rows = settle_all_but(client, gmail, 28, keep=4)
    decide(client, run_id, rows[0]["item_id"], "defer")

    group = group_of(client, run_id)
    shown = len(group["screen"]["rows"])

    assert group["counts"]["remaining"] == shown
    assert group["screen"]["footer"].startswith(f"{shown} items")
    assert {row["item_id"] for row in group["screen"]["rows"]} == {
        item["item_id"] for item in group["items"] if item["state"] in {"pending", "approved"}
    }


def test_answering_the_last_row_moves_on_and_keeps_no_older_count(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, rows = settle_all_but(client, gmail, 28, keep=4)

    for row in rows[:4]:
        decided = client.post(
            f"/review/runs/{run_id}/items/{row['item_id']}/decision",
            headers=AUTH,
            json={"decision": "dismiss"},
        )
        assert decided.status_code == 200, decided.text

    run = decided.json()["run"]
    group = group_of(client, run_id)

    assert group["state"] == "completed"
    assert group["counts"]["remaining"] == 0
    assert run["current_group"] is None or run["current_group"]["capability_key"] != "admin"
    assert "28" not in json.dumps(run["current_group"])
    assert "28" not in group["screen"]["footer"]


def test_deleting_nineteen_of_twenty_two_rows_leaves_the_other_three_alone(
    client: TestClient, gmail: FakeGmail
) -> None:
    """The regression: rows 1-3 and 5-20 of 22, and only those, reach Gmail.

    Twenty-two rows were approved and nineteen were asked for. Every stage —
    decision, preparation, execution, verification — must be the same
    nineteen, and rows 4, 21 and 22 must be exactly as they were.
    """
    run_id, rows = start_with_threads(client, gmail, 22)
    chosen = [rows[index] for index in [0, 1, 2, *range(4, 20)]]
    untouched = [rows[3], rows[20], rows[21]]
    approve_trash(client, run_id, rows)
    selected = [row["item_id"] for row in chosen]

    assert len(selected) == 19

    prepared = prepare(client, run_id, selected)

    assert prepared["requested_item_ids"] == selected
    assert prepared["prepared_item_ids"] == selected
    assert len(prepared["action_ids"]) == 19
    assert prepared["excluded_items"] == []
    assert prepared["scope_matches_request"] is True
    assert prepared["entire_capability"] is False
    assert gmail.writes == []

    executed = execute(client, run_id, prepared, item_ids=selected)

    assert executed.status_code == 200, executed.text
    assert [action["state"] for action in executed.json()["actions"]] == ["completed"] * 19
    assert sorted(gmail.writes) == sorted(("trash", row["thread_id"]) for row in chosen)

    for row in untouched:
        assert gmail.thread_labels[row["thread_id"]] == ["INBOX", ADMIN_LABEL_ID]

    counts = client.get(f"/review/runs/{run_id}", headers=AUTH).json()["groups"][0]["counts"]
    assert counts == {"total": 22, "executed": 19, "approved": 3, "remaining": 3}


def test_a_preparation_superseded_by_a_later_one_executes_nothing(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Preparing again is a new selection, and it retires the old one."""
    run_id, rows = start_with_threads(client, gmail, 3)
    approve_trash(client, run_id, rows)
    stale = prepare(client, run_id, [rows[0]["item_id"]])
    current = prepare(client, run_id, [rows[1]["item_id"]])

    refused = execute(client, run_id, stale)

    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "ScopeMismatch"
    assert "superseded" in refused.json()["detail"]["message"]
    assert gmail.writes == []

    executed = execute(client, run_id, current)

    assert executed.status_code == 200, executed.text
    assert gmail.writes == [("trash", rows[1]["thread_id"])]


def test_a_scope_this_run_never_prepared_is_not_found(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, rows = start_with_threads(client, gmail, 2)
    approve_trash(client, run_id, rows)

    refused = client.post(
        f"/review/runs/{run_id}/actions/execute",
        headers=AUTH,
        json={
            "scope_id": "scope-from-somewhere-else",
            "item_ids": [rows[0]["item_id"]],
            "action_ids": ["an-action-from-somewhere-else"],
            "confirm": True,
        },
    )

    assert refused.status_code == 404
    assert gmail.writes == []


def test_preparation_refuses_to_read_a_capability_as_a_selection(
    client: TestClient, gmail: FakeGmail
) -> None:
    """A capability key on its own is not a selection, and cannot become one."""
    run_id, rows = start_with_threads(client, gmail, 3)
    approve_trash(client, run_id, rows)
    path = f"/review/runs/{run_id}/actions/prepare"

    unspecified = client.post(path, headers=AUTH, json={"capability_key": "admin"})
    empty = client.post(path, headers=AUTH, json={"capability_key": "admin", "item_ids": []})
    both = client.post(
        path,
        headers=AUTH,
        json={
            "capability_key": "admin",
            "item_ids": [rows[0]["item_id"]],
            "entire_capability": True,
        },
    )

    assert unspecified.status_code == 400
    assert "exact item_ids" in unspecified.json()["detail"]
    assert empty.status_code == 400
    assert both.status_code == 400
    assert client.get(f"/review/runs/{run_id}/actions", headers=AUTH).json()["counts"] == {
        "total": 0
    }
    assert gmail.writes == []


def test_the_whole_capability_has_to_be_asked_for_by_name(
    client: TestClient, gmail: FakeGmail
) -> None:
    """"All of them" is available, but only as an explicit sentence."""
    run_id, rows = start_with_threads(client, gmail, 3)
    approve_trash(client, run_id, rows[:2])

    prepared = client.post(
        f"/review/runs/{run_id}/actions/prepare",
        headers=AUTH,
        json={"capability_key": "admin", "entire_capability": True},
    ).json()

    assert prepared["entire_capability"] is True
    assert sorted(prepared["prepared_item_ids"]) == sorted(
        row["item_id"] for row in rows[:2]
    )
    assert prepared["scope_matches_request"] is True
    assert gmail.writes == []


def test_confirming_a_selection_that_was_not_prepared_executes_nothing(
    client: TestClient, gmail: FakeGmail
) -> None:
    """The confirmation restates the rows, and the restatement has to agree."""
    run_id, rows = start_with_threads(client, gmail, 3)
    approve_trash(client, run_id, rows)
    prepared = prepare(client, run_id, [rows[0]["item_id"]])

    refused = execute(
        client,
        run_id,
        prepared,
        item_ids=[rows[0]["item_id"], rows[1]["item_id"]],
    )

    detail = refused.json()["detail"]
    assert refused.status_code == 409
    assert detail["error"] == "ScopeMismatch"
    assert detail["not_prepared"] == [rows[1]["item_id"]]
    assert gmail.writes == []


def test_action_ids_from_another_preparation_are_refused(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, rows = start_with_threads(client, gmail, 2)
    approve_trash(client, run_id, rows)
    first = prepare(client, run_id, [rows[0]["item_id"]])
    second = prepare(client, run_id, [rows[1]["item_id"]])

    refused = execute(
        client, run_id, second, action_ids=first["action_ids"]
    )

    detail = refused.json()["detail"]
    assert refused.status_code == 409
    assert detail["error"] == "ScopeMismatch"
    assert detail["not_prepared"] == first["action_ids"]
    assert gmail.writes == []


def test_a_row_decided_again_after_preparation_stops_the_whole_scope(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Changing one row's mind invalidates the confirmation, not part of it."""
    run_id, rows = start_with_threads(client, gmail, 3)
    approve_trash(client, run_id, rows)
    prepared = prepare(client, run_id, [row["item_id"] for row in rows])

    client.post(
        f"/review/runs/{run_id}/items/{rows[1]['item_id']}/decision",
        headers=AUTH,
        json={"decision": "dismiss"},
    )
    refused = execute(client, run_id, prepared)

    detail = refused.json()["detail"]
    assert refused.status_code == 409
    assert detail["error"] == "ScopeMismatch"
    assert detail["no_longer_approved"] == [rows[1]["item_id"]]
    assert gmail.writes == []


def test_a_trashed_thread_can_be_taken_back_out_of_trash(
    client: TestClient, gmail: FakeGmail
) -> None:
    """The undo runs through the same lifecycle as the thing it undoes."""
    run_id, item_id = approved_run(
        client, gmail, decision="override", action="move_gmail_thread_to_trash"
    )
    execute(client, run_id, prepare(client, run_id))

    assert "TRASH" in gmail.thread_labels["t1"]

    group = client.get(f"/review/runs/{run_id}/groups/admin", headers=AUTH).json()
    restorable = group["restorable"]

    assert [entry["item_id"] for entry in restorable] == [item_id]
    assert restorable[0]["action"] == "restore_gmail_thread_from_trash"

    undo = client.post(restorable[0]["path"], headers=AUTH, json=restorable[0]["body"])

    assert undo.status_code == 200, undo.text
    assert undo.json()["decided"][0]["approved_action"] == "gmail.untrash"
    assert gmail.writes == [("trash", "t1")]

    prepared = prepare(client, run_id, [item_id])
    executed = execute(client, run_id, prepared, item_ids=[item_id])

    assert executed.status_code == 200, executed.text
    assert executed.json()["actions"][0]["state"] == "completed"
    assert gmail.writes == [("trash", "t1"), ("untrash", "t1")]
    assert "TRASH" not in gmail.thread_labels["t1"]


def test_restoring_a_thread_already_out_of_trash_writes_nothing(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Gmail's state is the authority: an undone Trash is verified, not redone."""
    run_id, item_id = approved_run(
        client, gmail, decision="override", action="move_gmail_thread_to_trash"
    )
    execute(client, run_id, prepare(client, run_id))
    gmail.thread_labels["t1"] = ["INBOX", ADMIN_LABEL_ID]
    client.post(
        f"/review/runs/{run_id}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "override", "action": "restore_gmail_thread_from_trash"},
    )

    prepared = prepare(client, run_id, [item_id])
    executed = execute(client, run_id, prepared)

    action = executed.json()["actions"][0]
    assert action["state"] == "completed"
    assert action["verification"] == {"labels": ["INBOX", ADMIN_LABEL_ID]}
    assert gmail.writes == [("trash", "t1")]


def test_a_capability_that_may_not_restore_offers_nothing_to_restore(
    client: TestClient, gmail: FakeGmail
) -> None:
    """What can be undone is a permission, not an assumption about Gmail."""
    gmail.serve("t1", "Newsletter: weekly")
    body = opened(client)

    taxes = client.get(
        f"/review/runs/{body['run_id']}/groups/financial_taxes", headers=AUTH
    )

    assert taxes.status_code == 200, taxes.text
    assert taxes.json()["restorable"] == []
    assert "gmail.untrash" not in taxes.json()["allowed_actions"]


def test_a_scope_can_be_read_back_to_see_whether_it_still_stands(
    client: TestClient, gmail: FakeGmail
) -> None:
    run_id, rows = start_with_threads(client, gmail, 2)
    approve_trash(client, run_id, rows)
    first = prepare(client, run_id, [rows[0]["item_id"]])

    read = client.get(f"/review/runs/{run_id}/scopes/{first['scope_id']}", headers=AUTH)

    assert read.status_code == 200, read.text
    assert read.json()["state"] == "current"
    assert read.json()["prepared_item_ids"] == [rows[0]["item_id"]]

    prepare(client, run_id, [rows[1]["item_id"]])
    reread = client.get(f"/review/runs/{run_id}/scopes/{first['scope_id']}", headers=AUTH)

    assert reread.json()["state"] == "superseded"
    assert gmail.writes == []


def test_a_scope_prepared_before_a_restart_cannot_execute(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Restarting the day disarms the confirmation that was already in hand."""
    run_id, _ = approved_run(client, gmail)
    prepared = prepare(client, run_id)

    restarted = client.post("/review/restart", headers=AUTH, json={})
    assert restarted.status_code == 200, restarted.text

    response = execute(client, run_id, prepared)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "ReviewAbandoned"
    assert gmail.writes == []


def test_an_abandoned_review_prepares_nothing(client: TestClient, gmail: FakeGmail) -> None:
    run_id, item_id = approved_run(client, gmail)
    client.post("/review/restart", headers=AUTH, json={})

    response = client.post(
        f"/review/runs/{run_id}/actions/prepare",
        headers=AUTH,
        json={"capability_key": "admin", "item_ids": [item_id]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "ReviewAbandoned"
    assert gmail.writes == []

def test_deciding_three_rows_holds_the_review_until_they_are_carried_out(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Brian's incident, end to end: decided, still in the inbox, then done.

    Three rows are told to go to Trash and the review stays on Admin, saying
    what has not happened and how to make it happen. Only the execution
    empties the group and lets the review move on.
    """
    for number in (1, 2, 3):
        gmail.serve(f"t{number}", f"Newsletter: issue {number}")
    body = opened(client)
    run_id = body["run_id"]

    decided = client.post(
        f"/review/runs/{run_id}/groups/admin/decisions",
        headers=AUTH,
        json={"decision": "override", "action": "move_gmail_thread_to_trash"},
    ).json()

    run = decided["run"]
    outstanding = run["outstanding_execution"][0]
    assert run["current_group"]["capability_key"] == "admin"
    assert run["current_group"]["state"] == "awaiting_actions"
    assert len(outstanding["item_ids"]) == 3
    assert "Nothing has changed in Gmail" in outstanding["message"]
    assert gmail.writes == []

    prepared = client.post(
        outstanding["path"], headers=AUTH, json=outstanding["body"]
    ).json()

    assert sorted(prepared["prepared_item_ids"]) == sorted(outstanding["item_ids"])
    assert prepared["scope_matches_request"] is True
    assert gmail.writes == []

    executed = execute(client, run_id, prepared)

    assert executed.status_code == 200, executed.text
    assert sorted(gmail.writes) == sorted(("trash", f"t{number}") for number in (1, 2, 3))

    after = client.get(f"/review/runs/{run_id}", headers=AUTH).json()

    assert after["outstanding_execution"] == []
    assert after["state"] == "completed"


def test_a_group_says_it_is_decided_then_prepared_then_done(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Four states of the same row, and a sentence that tells them apart.

    Deciding, preparing, executing and verifying are steps, and a review that
    describes them all the same way is how three threads came to be reported
    as deleted while sitting in the inbox.
    """
    run_id, _ = approved_run(client, gmail)

    decided = client.get(f"/review/runs/{run_id}/groups/admin", headers=AUTH).json()
    assert "1 decision recorded and not carried out" in decided["standing"]

    prepared = prepare(client, run_id)
    waiting = client.get(f"/review/runs/{run_id}/groups/admin", headers=AUTH).json()
    assert "1 action prepared and awaiting your confirmation" in waiting["standing"]

    execute(client, run_id, prepared)
    done = client.get(f"/review/runs/{run_id}/groups/admin", headers=AUTH).json()
    assert done["standing"] == "Admin is complete. 1 action was executed and verified."


def test_the_summary_counts_the_archive_only_once_gmail_confirms_it(
    client: TestClient, gmail: FakeGmail
) -> None:
    """Every completion count comes from verified execution, or from nothing."""
    run_id, _ = approved_run(client, gmail)

    summary = client.get(f"/review/runs/{run_id}", headers=AUTH).json()["summary"]
    assert summary["done"] == {}
    assert summary["standing"]["decided_not_executed"] == 1

    prepared = prepare(client, run_id)
    waiting = client.get(f"/review/runs/{run_id}", headers=AUTH).json()["summary"]
    assert waiting["done"] == {}
    assert waiting["standing"] == {
        "decided_not_executed": 0,
        "prepared_awaiting_confirmation": 1,
        "failed_or_unverified": 0,
    }

    execute(client, run_id, prepared)
    finished = client.get(f"/review/runs/{run_id}", headers=AUTH).json()["summary"]
    assert finished["done"] == {"archived": 1}
    assert finished["standing"]["prepared_awaiting_confirmation"] == 0
    assert "not finished" not in finished["message"]
