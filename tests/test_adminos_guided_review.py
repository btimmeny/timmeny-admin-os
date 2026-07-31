"""A review whose reading is done by ChatGPT and whose process is owned here.

What these hold to: starting admin always reads the mailbox afresh, a review is
held to the playbook version it pinned and not the one in force later, a
submission that is wrong in any way is refused whole, a phase cannot be
completed on nothing, and a phase nobody built is never reported as done.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from adminos.capabilities.config import clear_cache
from adminos.db import engine as engine_module
from adminos.db.models import GuidedReviewEvent, GuidedReviewItem, GuidedReviewSnapshot
from adminos.domain.guided_review import read_review, start_review
from adminos.domain.review_playbook import (
    EMAIL_REVIEW,
    Disposition,
    ItemField,
    ReviewPlaybookDocument,
    Urgency,
)
from adminos.domain.review_playbook_store import (
    read_active_review_playbook,
    revise_review_playbook,
)
from adminos.mcp import protocol
from adminos.mcp.tools import TOOL_NAMES


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPOSITORY_ROOT / "tests/data/capabilities_actions.yaml"
INSTRUCTIONS_PATH = REPOSITORY_ROOT / "docs/gpt-admin-review-instructions.md"
INSTRUCTION_LIMIT = 8000
API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}

OBSERVED_AT = datetime(2026, 7, 29, 8, 30, tzinfo=UTC)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'guided.db'}"
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


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Any]:
    url = f"sqlite:///{tmp_path / 'guided-direct.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    factory = sessionmaker(bind=create_engine(url))
    with factory() as session:
        yield session


def call(client: TestClient, name: str, arguments: dict[str, Any] | None = None) -> Any:
    response = client.post(
        "/mcp",
        headers=AUTH,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def payload(result: Any) -> dict[str, Any]:
    return result["structuredContent"]


def start(client: TestClient) -> dict[str, Any]:
    return payload(call(client, "start_admin_review", {"fresh": True}))


def item(thread_id: str, group_key: str = "act_now", **overrides: Any) -> dict[str, Any]:
    body = {
        "source_thread_id": thread_id,
        "subject": f"Subject {thread_id}",
        "sender": "someone@example.com",
        "received_at": OBSERVED_AT.isoformat(),
        "group_key": group_key,
        "summary": "What the thread says.",
        "why_it_matters": "Why Brian should care.",
        "recommended_next_action": "Reply to it.",
        "recommended_gmail_disposition": Disposition.KEEP_IN_INBOX.value,
        "task_required": True,
        "urgency": Urgency.NORMAL.value,
        "confidence": 0.9,
        "uncertainties": [],
    }
    body.update(overrides)
    return body


def submission(review: dict[str, Any], items: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "review_id": review["review_id"],
        "playbook_version_id": review["playbook_version_id"],
        "source_snapshot": {
            "source": "gmail",
            "mailbox_scope": "inbox_only",
            "observed_at": OBSERVED_AT.isoformat(),
            "thread_count": len(items),
        },
        "items": items,
        "recommended_order": [entry["source_thread_id"] for entry in items],
    }
    body.update(overrides)
    return body


def record(client: TestClient, body: dict[str, Any]) -> Any:
    return call(client, "record_email_review", body)


def codes(result: Any) -> list[str]:
    return [failure["code"] for failure in payload(result)["failures"]]


def test_the_mcp_endpoint_needs_the_api_key(client: TestClient) -> None:
    unauthenticated = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert unauthenticated.status_code == 401


def test_initialize_agrees_a_protocol_version_and_names_the_server(
    client: TestClient,
) -> None:
    response = client.post(
        "/mcp",
        headers=AUTH,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
        },
    )
    result = response.json()["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == protocol.SERVER_NAME
    assert result["capabilities"]["tools"] == {"listChanged": False}


def test_the_server_publishes_exactly_the_five_tools(client: TestClient) -> None:
    response = client.post(
        "/mcp", headers=AUTH, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    published = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert published == [
        "start_admin_review",
        "read_admin_review",
        "read_review_playbook",
        "record_email_review",
        "complete_review_phase",
    ]
    assert published == list(TOOL_NAMES)


def test_every_published_schema_is_self_contained(client: TestClient) -> None:
    """A reference an importer will not follow is a tool it cannot call."""
    response = client.post(
        "/mcp", headers=AUTH, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    for tool in response.json()["result"]["tools"]:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "$defs" not in schema
        assert "$ref" not in repr(schema), tool["name"]


def test_a_notification_is_answered_with_nothing(client: TestClient) -> None:
    response = client.post(
        "/mcp", headers=AUTH, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert response.status_code == 202
    assert response.content == b""


def test_an_unknown_method_is_refused_rather_than_ignored(client: TestClient) -> None:
    response = client.post(
        "/mcp", headers=AUTH, json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"}
    )
    assert response.json()["error"]["code"] == protocol.METHOD_NOT_FOUND


def test_a_client_that_reads_a_stream_is_answered_with_one(client: TestClient) -> None:
    """The remote MCP clients that matter send this Accept header and stream."""
    response = client.post(
        "/mcp",
        headers={**AUTH, "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["MCP-Protocol-Version"] == protocol.PROTOCOL_VERSION
    body = response.text
    assert body.startswith("event: message\ndata: ")
    answer = json.loads(body.split("data: ", 1)[1].strip())
    assert [tool["name"] for tool in answer["result"]["tools"]] == list(TOOL_NAMES)


def test_a_client_that_reads_json_is_answered_with_json(client: TestClient) -> None:
    response = client.post(
        "/mcp",
        headers={**AUTH, "Accept": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )

    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_a_browser_client_is_told_it_may_call(client: TestClient) -> None:
    """A preflight that goes unanswered is a connector that never connects."""
    response = client.options(
        "/mcp",
        headers={
            "Origin": "https://inspector.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_starting_admin_opens_a_review_pinned_to_a_playbook_version(
    client: TestClient,
) -> None:
    review = start(client)
    assert review["status"] == "in_progress"
    assert review["current_phase"] == EMAIL_REVIEW
    assert review["playbook_version_id"]
    assert review["next_operation"] == "read_review_playbook"
    assert review["snapshot_at"]


def test_the_later_phases_are_named_and_unavailable(client: TestClient) -> None:
    """Three quarters of the process is not built, and the review says so."""
    review = start(client)
    order = [(phase["phase_key"], phase["status"]) for phase in review["phase_order"]]
    assert order == [
        ("email_review", "ready"),
        ("monday_reconciliation", "unavailable"),
        ("todo_review", "unavailable"),
        ("daily_plan", "unavailable"),
    ]


def test_saying_hello_again_starts_a_new_review_and_keeps_the_old_one(
    client: TestClient,
) -> None:
    first = start(client)
    second = start(client)
    assert second["review_id"] != first["review_id"]
    assert second["supersedes_review_id"] == first["review_id"]

    earlier = payload(call(client, "read_admin_review", {"review_id": first["review_id"]}))
    assert earlier["status"] == "superseded"
    assert earlier["next_operation"] is None


def test_a_stale_review_will_not_take_a_submission(client: TestClient) -> None:
    first = start(client)
    start(client)
    refused = record(client, submission(first, [item("t1")]))
    assert refused["isError"] is True
    assert "REVIEW_NOT_ACTIVE" in codes(refused)


def test_the_playbook_returns_the_groups_in_configured_order(client: TestClient) -> None:
    review = start(client)
    phase = payload(
        call(client, "read_review_playbook", {"review_id": review["review_id"]})
    )["phase"]

    assert phase["source"] == {"app": "gmail", "mailbox_scope": "inbox_only"}
    keys = [group["key"] for group in phase["groups"]]
    assert keys == [
        "admin_cleanup",
        "act_now",
        "decisions",
        "legal",
        "financial",
        "career",
        "awaiting_confirmation",
        "waiting_on_others",
        "informational",
        "archive_or_trash",
        "remaining_inbox",
    ]
    assert keys[-1] == "remaining_inbox"
    assert phase["groups"][-1]["catch_all"] is True
    assert phase["completion_criteria"]["catch_all_required"] is True
    assert phase["rendering"]["show_group_counts"] is True


def test_the_playbook_states_every_required_field_and_the_allowed_values(
    client: TestClient,
) -> None:
    review = start(client)
    phase = payload(
        call(client, "read_review_playbook", {"review_id": review["review_id"]})
    )["phase"]

    assert set(phase["required_item_fields"]) == {field.value for field in ItemField}
    assert phase["allowed_values"]["urgency"] == [value.value for value in Urgency]
    assert phase["allowed_values"]["recommended_gmail_disposition"] == [
        value.value for value in Disposition
    ]
    assert phase["execution"]["dispositions_are_recommendations"] is True


def test_the_groups_come_from_configuration_rather_than_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different playbook file yields different groups, with no code changed."""
    playbook = tmp_path / "review-playbook.yaml"
    playbook.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "playbook_id: brian-email-review",
                "name: A different review",
                "phases:",
                "  - phase_key: email_review",
                "    label: Email review",
                "    order: 10",
                "    source:",
                "      app: gmail",
                "      mailbox_scope: inbox_only",
                "    groups:",
                "      - key: only_group",
                "        label: The only group",
                "        order: 10",
                "      - key: everything_else",
                "        label: Everything else",
                "        order: 20",
                "        catch_all: true",
                "    required_item_fields:",
                "      - summary",
            ]
        ),
        encoding="utf-8",
    )

    url = f"sqlite:///{tmp_path / 'configured.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("REVIEW_PLAYBOOK_PATH", str(playbook))
    clear_cache()
    engine_module.dispose_connection()
    try:
        client = TestClient(main.app)
        review = start(client)
        phase = payload(
            call(client, "read_review_playbook", {"review_id": review["review_id"]})
        )["phase"]
        assert [group["key"] for group in phase["groups"]] == [
            "only_group",
            "everything_else",
        ]
        assert phase["required_item_fields"] == [
            "source_thread_id",
            "group_key",
            "summary",
        ]
    finally:
        engine_module.dispose_connection()
        clear_cache()


def test_an_unavailable_phase_says_so_and_offers_no_groups(client: TestClient) -> None:
    review = start(client)
    phase = payload(
        call(
            client,
            "read_review_playbook",
            {"review_id": review["review_id"], "phase_key": "monday_reconciliation"},
        )
    )["phase"]
    assert phase["status"] == "unavailable"
    assert "groups" not in phase
    assert "not implemented" in phase["message"]


def test_a_complete_review_is_recorded_with_its_counts(client: TestClient) -> None:
    review = start(client)
    items = [item("t1"), item("t2", group_key="legal"), item("t3", group_key="remaining_inbox")]
    recorded = payload(record(client, submission(review, items)))

    assert recorded["status"] == "recorded"
    assert recorded["item_count"] == 3
    assert recorded["counts_by_group"] == {"act_now": 1, "legal": 1, "remaining_inbox": 1}
    assert recorded["executed"] is False
    assert recorded["next_operation"] == "complete_review_phase"


def test_an_empty_inbox_is_a_review_of_nothing_rather_than_a_refusal(
    client: TestClient,
) -> None:
    review = start(client)
    recorded = payload(record(client, submission(review, [])))
    assert recorded["status"] == "recorded"
    assert recorded["item_count"] == 0
    assert recorded["counts_by_group"] == {}


def test_the_catch_all_takes_a_thread_that_fits_nowhere(client: TestClient) -> None:
    review = start(client)
    recorded = payload(
        record(client, submission(review, [item("t1", group_key="remaining_inbox")]))
    )
    assert recorded["counts_by_group"] == {"remaining_inbox": 1}
    assert any("no other group" in warning for warning in recorded["validation_warnings"])


def test_a_group_the_playbook_does_not_have_is_refused(client: TestClient) -> None:
    review = start(client)
    refused = record(client, submission(review, [item("t1", group_key="invented_group")]))
    assert refused["isError"] is True
    assert "UNKNOWN_GROUP" in codes(refused)


def test_the_same_thread_twice_is_refused(client: TestClient) -> None:
    review = start(client)
    refused = record(client, submission(review, [item("t1"), item("t1")]))
    assert "DUPLICATE_THREAD" in codes(refused)


def test_a_count_that_disagrees_with_what_was_sent_is_refused(
    client: TestClient,
) -> None:
    """Two numbers that should agree are how a dropped thread announces itself."""
    review = start(client)
    body = submission(review, [item("t1")])
    body["source_snapshot"]["thread_count"] = 4
    refused = record(client, body)
    assert "COUNT_MISMATCH" in codes(refused)


def test_a_missing_required_field_is_refused_and_named(client: TestClient) -> None:
    review = start(client)
    refused = record(client, submission(review, [item("t1", why_it_matters=None)]))
    assert "MISSING_FIELD" in codes(refused)
    assert any(
        failure["path"] == "items[0].why_it_matters"
        for failure in payload(refused)["failures"]
    )


def test_a_field_left_blank_is_a_missing_field(client: TestClient) -> None:
    review = start(client)
    refused = record(client, submission(review, [item("t1", summary="   ")]))
    assert "MISSING_FIELD" in codes(refused)


def test_a_review_of_a_different_scope_is_refused(client: TestClient) -> None:
    review = start(client)
    body = submission(review, [item("t1")])
    body["source_snapshot"]["mailbox_scope"] = "everything"
    refused = record(client, body)
    assert refused["isError"] is True


def test_an_item_left_out_of_the_recommended_order_is_refused(
    client: TestClient,
) -> None:
    review = start(client)
    body = submission(review, [item("t1"), item("t2")])
    body["recommended_order"] = ["t1"]
    assert "ORDER_INCOMPLETE" in codes(record(client, body))


def test_an_order_naming_mail_that_is_not_in_the_review_is_refused(
    client: TestClient,
) -> None:
    review = start(client)
    body = submission(review, [item("t1")])
    body["recommended_order"] = ["t1", "t9"]
    assert "ORDER_UNKNOWN_ITEM" in codes(record(client, body))


def test_an_order_naming_the_same_thread_twice_is_refused(client: TestClient) -> None:
    review = start(client)
    body = submission(review, [item("t1"), item("t2")])
    body["recommended_order"] = ["t1", "t1", "t2"]
    assert "ORDER_DUPLICATE" in codes(record(client, body))


def test_a_submission_against_another_playbook_version_is_refused(
    client: TestClient,
) -> None:
    review = start(client)
    body = submission(review, [item("t1")], playbook_version_id="some-other-version")
    assert "STALE_PLAYBOOK_VERSION" in codes(record(client, body))


def test_a_refused_submission_records_nothing_at_all(
    client: TestClient, tmp_path: Path
) -> None:
    review = start(client)
    body = submission(review, [item("t1"), item("t2", group_key="invented_group")])
    assert record(client, body)["isError"] is True

    state = payload(call(client, "read_admin_review", {"review_id": review["review_id"]}))
    assert state["source_snapshot"] is None
    assert state["counts_by_group"] == {}
    assert state["validation"]["state"] == "rejected"
    assert state["next_operation"] == "read_review_playbook"


def test_a_refusal_names_every_fault_rather_than_the_first(client: TestClient) -> None:
    review = start(client)
    body = submission(
        review,
        [item("t1", group_key="invented_group"), item("t2", summary=None)],
        playbook_version_id="stale",
    )
    found = set(codes(record(client, body)))
    assert {"STALE_PLAYBOOK_VERSION", "UNKNOWN_GROUP", "MISSING_FIELD"} <= found


def test_a_recorded_review_reads_back_with_its_snapshot(client: TestClient) -> None:
    review = start(client)
    record(client, submission(review, [item("t1"), item("t2", group_key="legal")]))

    state = payload(call(client, "read_admin_review", {"review_id": review["review_id"]}))
    assert state["source_snapshot"]["thread_count"] == 2
    assert state["source_snapshot"]["mailbox_scope"] == "inbox_only"
    assert state["counts_by_group"] == {"act_now": 1, "legal": 1}
    assert state["validation"]["state"] == "recorded"
    assert state["next_operation"] == "complete_review_phase"


def test_reading_a_review_returns_no_mail(client: TestClient) -> None:
    """Admin OS holds an interpretation of the mailbox, and never shows the mail."""
    review = start(client)
    record(client, submission(review, [item("t1", subject="A private subject line")]))
    state = call(client, "read_admin_review", {"review_id": review["review_id"]})
    assert "A private subject line" not in repr(state)


def test_a_phase_cannot_be_completed_before_anything_is_recorded(
    client: TestClient,
) -> None:
    review = start(client)
    refused = call(
        client, "complete_review_phase", {"review_id": review["review_id"]}
    )
    assert refused["isError"] is True
    assert "no recorded result" in payload(refused)["message"]


def test_completing_the_email_phase_does_not_complete_the_review(
    client: TestClient,
) -> None:
    review = start(client)
    record(client, submission(review, [item("t1")]))
    completion = payload(
        call(client, "complete_review_phase", {"review_id": review["review_id"]})
    )

    assert completion["completed_phase"] == EMAIL_REVIEW
    assert completion["next_phase"] == "monday_reconciliation"
    assert completion["next_phase_status"] == "unavailable"
    assert completion["review_status"] == "partially_complete"
    assert "not implemented" in completion["message"]


def test_a_completed_phase_will_not_take_another_submission(
    client: TestClient,
) -> None:
    review = start(client)
    record(client, submission(review, [item("t1")]))
    call(client, "complete_review_phase", {"review_id": review["review_id"]})

    refused = record(client, submission(review, [item("t1")]))
    assert "WRONG_PHASE" in codes(refused)


def test_a_phase_nobody_built_cannot_be_completed(client: TestClient) -> None:
    review = start(client)
    refused = call(
        client,
        "complete_review_phase",
        {"review_id": review["review_id"], "phase_key": "todo_review"},
    )
    assert refused["isError"] is True
    assert "not implemented" in payload(refused)["message"]


def test_resubmitting_replaces_the_reading_and_keeps_the_earlier_one(
    client: TestClient, tmp_path: Path
) -> None:
    """A second reading of the mailbox is a new snapshot, not an edit of the first."""
    review = start(client)
    record(client, submission(review, [item("t1")]))
    record(client, submission(review, [item("t1"), item("t2")]))

    state = payload(call(client, "read_admin_review", {"review_id": review["review_id"]}))
    assert state["source_snapshot"]["thread_count"] == 2

    url = f"sqlite:///{tmp_path / 'guided.db'}"
    with sessionmaker(bind=create_engine(url))() as session:
        snapshots = session.query(GuidedReviewSnapshot).all()
        assert len(snapshots) == 2
        assert sum(1 for snapshot in snapshots if snapshot.superseded_at is not None) == 1
        assert session.query(GuidedReviewItem).count() == 3


def test_a_later_playbook_change_does_not_touch_a_review_under_way(
    database: Any,
) -> None:
    """The version a review pinned is the version it is held to, for its life."""
    started = start_review(database, fresh=True)
    pinned = started.review.playbook_revision_id
    original = [group.key for group in started.playbook.document.phase(EMAIL_REVIEW).groups]

    current = read_active_review_playbook(database).document
    phases = []
    for phase in current.phases:
        if phase.phase_key != EMAIL_REVIEW:
            phases.append(phase)
            continue
        phases.append(
            phase.model_copy(
                update={"groups": [group for group in phase.groups if group.catch_all]}
            )
        )
    revised = ReviewPlaybookDocument(
        schema_version=current.schema_version,
        playbook_id=current.playbook_id,
        name=current.name,
        phases=phases,
    )
    revise_review_playbook(
        database, revised, summary=["Fewer groups."], actor="human"
    )

    view = read_review(database, started.review.id)
    assert view.review.playbook_revision_id == pinned
    assert [
        group.key for group in view.playbook.document.phase(EMAIL_REVIEW).groups
    ] == original


def test_a_new_review_takes_the_playbook_now_in_force(database: Any) -> None:
    first = start_review(database, fresh=True)
    current = read_active_review_playbook(database).document
    revised = current.model_copy(update={"name": "The review, revised"})
    revise_review_playbook(database, revised, summary=["Renamed."], actor="human")

    second = start_review(database, fresh=True, now=datetime.now(UTC) + timedelta(minutes=1))
    assert second.review.playbook_revision_id != first.review.playbook_revision_id
    assert second.playbook.document.name == "The review, revised"


def test_the_audit_trail_records_what_happened(client: TestClient, tmp_path: Path) -> None:
    review = start(client)
    record(client, submission(review, [item("t1", group_key="invented_group")]))
    record(client, submission(review, [item("t1")]))
    call(client, "complete_review_phase", {"review_id": review["review_id"]})

    url = f"sqlite:///{tmp_path / 'guided.db'}"
    with sessionmaker(bind=create_engine(url))() as session:
        kinds = [
            event.kind
            for event in session.query(GuidedReviewEvent)
            .order_by(GuidedReviewEvent.sequence)
            .all()
        ]
    assert kinds[:3] == ["review_started", "playbook_loaded", "phase_started"]
    assert "validation_failed" in kinds
    assert "source_snapshot_recorded" in kinds
    assert "email_review_recorded" in kinds
    assert kinds[-1] == "phase_completed"


def test_starting_without_fresh_is_refused_rather_than_resuming(
    client: TestClient,
) -> None:
    refused = call(client, "start_admin_review", {"fresh": False})
    assert refused["isError"] is True
    assert "read_admin_review" in payload(refused)["message"]


def test_a_review_that_does_not_exist_is_a_refusal_not_a_crash(
    client: TestClient,
) -> None:
    refused = call(client, "read_admin_review", {"review_id": "no-such-review"})
    assert refused["isError"] is True
    assert payload(refused)["status"] == "refused"


def test_an_argument_the_tool_cannot_read_comes_back_as_a_refusal(
    client: TestClient,
) -> None:
    refused = call(client, "read_admin_review", {"review": "wrong-key"})
    assert refused["isError"] is True
    assert payload(refused)["status"] == "rejected"


def test_the_published_tools_are_readable_without_json_rpc(client: TestClient) -> None:
    response = client.get("/mcp/tools", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["tool_names"] == list(TOOL_NAMES)


def test_the_instructions_name_the_tools_that_exist_and_no_others() -> None:
    """A tool named in the instructions and absent from the server stalls a review."""
    instructions = INSTRUCTIONS_PATH.read_text()
    named = set(re.findall(r"`([a-z_]+_(?:review|phase|playbook))`", instructions))

    assert named == set(TOOL_NAMES), (
        f"The instructions call {sorted(named - set(TOOL_NAMES))} and never call "
        f"{sorted(set(TOOL_NAMES) - named)}."
    )
    assert len(instructions) <= INSTRUCTION_LIMIT, (
        f"{len(instructions)} characters, {len(instructions) - INSTRUCTION_LIMIT} over. "
        "Cut prose rather than a safeguard."
    )


def test_the_instructions_hold_the_line_the_tools_hold() -> None:
    """Both halves have to refuse the same things, and only the prose can drift."""
    instructions = INSTRUCTIONS_PATH.read_text().lower()

    for phrase in (
        "fresh: true",
        "exactly once",
        "catch-all",
        "recommendations",
        "never execute a gmail action",
        "source_snapshot.thread_count",
    ):
        assert phrase in instructions, f"The instructions never say {phrase!r}."
