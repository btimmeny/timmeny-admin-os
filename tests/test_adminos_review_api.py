from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Sequence

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import main
from adminos.adapters.gmail import GmailAuthError, GmailThread
from adminos.api import review as review_module
from adminos.capabilities.config import clear_cache
from adminos.db import engine as engine_module


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPOSITORY_ROOT / "tests/data/capabilities_pair.yaml"
API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}


class FakeGmailClient:
    """Serves canned threads per resolved label id."""

    def __init__(
        self,
        labels: dict[str, str] | None = None,
        threads: dict[str, GmailThread] | None = None,
        threads_by_label: dict[str, list[str]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.labels = labels or {}
        self.threads = threads or {}
        self.threads_by_label = threads_by_label or {}
        self.error = error
        self.queries: list[str | None] = []

    async def resolve_label_id(self, label_name: str) -> str | None:
        if self.error is not None:
            raise self.error
        return self.labels.get(label_name)

    async def list_thread_ids(
        self,
        label_ids: Sequence[str],
        limit: int,
        query: str | None = None,
    ) -> list[str]:
        self.queries.append(query)
        return self.threads_by_label.get(label_ids[-1], [])[:limit]

    async def fetch_thread(self, thread_id: str) -> GmailThread:
        return self.threads[thread_id]


def thread(
    thread_id: str,
    subject: str,
    label_ids: Sequence[str] = ("INBOX",),
) -> GmailThread:
    """A thread as Gmail returns it, labels and all.

    Labels are not decoration here: they are what decides whether a thread is
    in the review at all, so a test that wants an archived or trashed thread
    says so by giving it the labels one carries.
    """
    return GmailThread(
        thread_id=thread_id,
        message_id="m1",
        subject=subject,
        participants=["cpa@kpmg.com"],
        received_at=datetime(2026, 7, 20, tzinfo=UTC),
        snippet="Attached is the estimate.",
        label_ids=list(label_ids),
    )


def install_client(monkeypatch: pytest.MonkeyPatch, fake: FakeGmailClient) -> None:
    @asynccontextmanager
    async def open_client(_credentials: object) -> AsyncIterator[FakeGmailClient]:
        yield fake

    monkeypatch.setattr(review_module, "open_gmail_client", open_client)
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh-token")


def mailbox(monkeypatch: pytest.MonkeyPatch, **by_label: list[GmailThread]) -> FakeGmailClient:
    """Wire a mailbox where each capability's label serves its own threads."""
    label_ids = {"financial/taxes": "Label_9", "Admin": "Label_4"}
    threads = {item.thread_id: item for group in by_label.values() for item in group}
    fake = FakeGmailClient(
        labels=label_ids,
        threads=threads,
        threads_by_label={
            label_ids[label]: [item.thread_id for item in by_label.get(key, [])]
            for key, label in (("taxes", "financial/taxes"), ("admin", "Admin"))
        },
    )
    install_client(monkeypatch, fake)
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'review-api.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("CAPABILITIES_PATH", str(CONFIG_PATH))
    for name in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    clear_cache()
    engine_module.dispose_connection()

    yield TestClient(main.app)

    engine_module.dispose_connection()
    clear_cache()


def start(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/review/start", headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_starting_a_review_requires_authentication(client: TestClient) -> None:
    assert client.post("/review/start", json={}).status_code == 401


def test_start_presents_one_group_at_a_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Newsletter: weekly")],
    )

    body = start(client)

    assert [group["capability_key"] for group in body["groups"]] == ["financial_taxes", "admin"]
    assert body["current_group"]["capability_key"] == "financial_taxes"
    assert len(body["current_group"]["items"]) == 1


def test_start_records_the_configuration_that_produced_the_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    body = start(client)

    assert body["config_version"] == "test.pair"
    assert len(body["config_digest"]) == 64


def test_start_is_idempotent_within_a_day(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    first = start(client)
    second = start(client)

    assert first["run_id"] == second["run_id"]
    assert second["current_group"]["counts"]["total"] == 1


def test_start_without_gmail_credentials_still_opens_the_review(client: TestClient) -> None:
    body = start(client)

    assert body["state"] == "completed"
    assert "not configured" in body["warnings"][0]


def test_a_gmail_failure_warns_rather_than_failing_the_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_client(monkeypatch, FakeGmailClient(error=GmailAuthError("token rejected")))

    body = start(client)

    assert "token rejected" in body["warnings"][0]


def test_start_can_skip_the_mailbox_refresh(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    body = start(client, sync=False)

    assert body["warnings"] == []
    assert body["current_group"] is None


def test_a_group_can_be_fetched_on_its_own(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, admin=[thread("t2", "Newsletter: weekly")])
    run_id = start(client)["run_id"]

    response = client.get(f"/review/runs/{run_id}/groups/admin", headers=AUTH)

    body = response.json()
    assert response.status_code == 200
    assert body["capability_key"] == "admin"
    assert body["allowed_actions"] == ["gmail.label", "gmail.archive"]
    assert body["allow_bulk_decisions"] is False


def test_an_unknown_group_is_reported(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    run_id = start(client)["run_id"]

    assert client.get(f"/review/runs/{run_id}/groups/career", headers=AUTH).status_code == 404


def test_an_unknown_run_is_reported(client: TestClient) -> None:
    assert client.get("/review/runs/missing", headers=AUTH).status_code == 404


def test_the_recommendation_is_shown_with_its_provenance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    item = start(client)["current_group"]["items"][0]

    assert item["recommendation"] == "monday.create_task"
    assert item["recommendation_source"] == "policy"
    assert item["recommendation_confidence"] == 0.9
    assert item["objectives"] == ["financial_compliance"]
    assert item["requires_confirmation"] is True


def test_approving_an_item_records_the_action_without_executing_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    response = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "approve"},
    )

    decided = response.json()["decided"][0]
    assert response.status_code == 200
    assert decided["state"] == "approved"
    assert decided["approved_action"] == "monday.create_task"


def test_an_approved_action_leaves_the_run_awaiting_execution(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing in this increment executes; the run says so rather than claiming completion."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    response = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "approve"},
    )

    assert response.json()["run"]["state"] == "awaiting_actions"


def test_dismissing_completes_the_group(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    response = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "dismiss"},
    )

    assert response.json()["run"]["state"] == "completed"


def test_an_action_the_capability_cannot_take_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, admin=[thread("t2", "Newsletter: weekly")])
    body = start(client)
    group = client.get(f"/review/runs/{body['run_id']}/groups/admin", headers=AUTH).json()

    response = client.post(
        f"/review/runs/{body['run_id']}/items/{group['items'][0]['item_id']}/decision",
        headers=AUTH,
        json={"decision": "override", "action": "gmail.send_draft"},
    )

    assert response.status_code == 409
    assert "not allowed" in response.json()["detail"]


def test_an_undefined_action_is_rejected_before_it_reaches_the_domain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    response = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "override", "action": "gmail.delete_everything"},
    )

    assert response.status_code == 422


def test_bulk_decisions_settle_a_group_at_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities"), thread("t2", "KPMG follow-up")],
    )
    body = start(client)

    response = client.post(
        f"/review/runs/{body['run_id']}/groups/financial_taxes/decisions",
        headers=AUTH,
        json={"decision": "dismiss"},
    )

    assert response.status_code == 200
    assert len(response.json()["decided"]) == 2
    assert response.json()["run"]["state"] == "completed"


def test_bulk_decisions_are_refused_where_configuration_forbids_them(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, admin=[thread("t2", "Newsletter: weekly")])
    body = start(client)

    response = client.post(
        f"/review/runs/{body['run_id']}/groups/admin/decisions",
        headers=AUTH,
        json={"decision": "dismiss"},
    )

    assert response.status_code == 409
    assert "bulk" in response.json()["detail"]


def test_a_model_assessment_is_recorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "Quarterly note")])
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    response = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/assessment",
        headers=AUTH,
        json={
            "category": "reference",
            "confidence": 0.6,
            "rationale": "Background only.",
            "model_version": "gpt-test",
        },
    )

    assert response.status_code == 200
    assert response.json()["category"] == "reference"


def test_a_model_category_outside_the_policy_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "Quarterly note")])
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    response = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/assessment",
        headers=AUTH,
        json={
            "category": "invented",
            "confidence": 1.0,
            "rationale": "Made up.",
            "model_version": "gpt-test",
        },
    )

    assert response.status_code == 409


def test_an_assessment_cannot_decide_anything(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model may suggest; only a decision moves an item towards execution."""
    mailbox(monkeypatch, taxes=[thread("t1", "Quarterly note")])
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    assessed = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/assessment",
        headers=AUTH,
        json={
            "category": "obligation",
            "confidence": 1.0,
            "rationale": "They want a filing.",
            "model_version": "gpt-test",
            "recommendation": "monday.create_task",
        },
    ).json()

    assert assessed["recommendation"] == "monday.create_task"
    assert assessed["state"] == "pending"
    assert assessed["approved_action"] is None


def test_the_review_reports_no_message_content(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0003: the GPT sees metadata, not the mail itself."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    item = start(client)["current_group"]["items"][0]

    assert "snippet" not in item
    assert "body" not in item


def test_the_capability_configuration_is_visible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.get("/admin/capabilities", headers=AUTH)

    body = response.json()
    assert response.status_code == 200
    assert body["version"] == "test.pair"
    assert [capability["key"] for capability in body["capabilities"]] == [
        "financial_taxes",
        "admin",
    ]


def test_an_item_the_capability_auto_approves_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`requires_confirmation` is per item and per confidence, not per capability."""
    monkeypatch.setenv("CAPABILITIES_PATH", str(REPOSITORY_ROOT / "tests/data/capabilities_auto.yaml"))
    clear_cache()
    mailbox(monkeypatch, admin=[thread("t2", "Newsletter: weekly")])

    item = start(client)["current_group"]["items"][0]

    assert item["recommendation"] == "gmail.archive"
    assert item["requires_confirmation"] is False


def test_a_group_read_back_reports_its_current_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]
    client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "dismiss"},
    )

    group = client.get(
        f"/review/runs/{body['run_id']}/groups/financial_taxes", headers=AUTH
    ).json()

    assert group["state"] == "completed"
    assert group["counts"]["dismissed"] == 1


def test_every_review_response_carries_its_presentation_contract(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The screen travels with the data, so nothing downstream invents a layout."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    body = start(client)
    screen = body["current_group"]["screen"]

    assert body["screen_id"] == "test-review-v1"
    assert body["current_group"]["screen_id"] == "test-review-v1"
    assert screen["title"] == "Test review"
    assert [column["label"] for column in screen["columns"]] == [
        "#",
        "What it is",
        "Recommended Action",
        "Confidence",
        "Decision",
    ]
    assert len(screen["rows"][0]["cells"]) == len(screen["columns"])
    assert screen["footer"] == "1 item still need you."


def test_a_row_says_which_decisions_it_would_accept(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])

    row = start(client)["current_group"]["screen"]["rows"][0]

    assert row["actions"] == ["approve", "dismiss", "defer"]
    assert row["cells"][2] == "Create a Monday task"


def test_the_contract_names_the_call_a_decision_should_make(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Following the contract is enough: the renderer needs no route knowledge."""
    mailbox(monkeypatch, taxes=[thread("t1", "KPMG Activities")])
    body = start(client)
    screen = body["current_group"]["screen"]
    row = screen["rows"][0]
    dismiss = next(action for action in screen["actions"] if action["id"] == "dismiss")

    response = client.request(
        dismiss["method"],
        dismiss["path"].format(item_id=row["item_id"]),
        headers=AUTH,
        json=dismiss["body"],
    )

    assert response.status_code == 200, response.text
    assert response.json()["decided"][0]["state"] == "dismissed"


def test_a_decision_response_returns_the_next_screen(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox(
        monkeypatch,
        taxes=[thread("t1", "KPMG Activities")],
        admin=[thread("t2", "Newsletter: weekly")],
    )
    body = start(client)
    item_id = body["current_group"]["items"][0]["item_id"]

    decided = client.post(
        f"/review/runs/{body['run_id']}/items/{item_id}/decision",
        headers=AUTH,
        json={"decision": "dismiss"},
    ).json()

    assert decided["run"]["current_group"]["capability_key"] == "admin"
    assert decided["run"]["current_group"]["screen"]["rows"][0]["cells"][1] == (
        "Newsletter: weekly"
    )
