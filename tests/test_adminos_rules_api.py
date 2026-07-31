"""The rulebook over HTTP: one step of a rule's life per request.

What these hold to: every route needs the key, a refusal comes back as a
refusal rather than a crash, testing writes no mail, confirming does not
activate, and retiring says out loud that there is no way back.
"""

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
from adminos.db.models import Evidence


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPOSITORY_ROOT / "tests/data/capabilities_actions.yaml"
API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}

TRANSFER_PATTERN = (
    r"^Inter Institution Transfer Request (?P<number>\d+) "
    r"Will Occur in (?P<days>\d+) Days$"
)
TRANSFER_SUBJECT = "Inter Institution Transfer Request 207960765 Will Occur in 3 Days"

DRAFT: dict[str, Any] = {
    "name": "Inter-institution transfer notifications",
    "description": "Files transfer notices where the record of them belongs.",
    "rule_type": "email_filing_rule",
    "capability_key": "admin",
    "priority": 200,
    "match": {
        "all": [{"field": "subject", "operator": "regex", "value": TRANSFER_PATTERN}]
    },
    "effects": [{"kind": "recommend_action", "action": "gmail.archive"}],
    "change_reason": "Brian named a recurring transfer notice.",
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'rules-api.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    factory = sessionmaker(bind=create_engine(url))
    with factory() as session:
        session.add(
            Evidence(
                source_system="gmail",
                source_thread_id="t1",
                subject=TRANSFER_SUBJECT,
                participants=["alerts@fidelity.example"],
                received_at=datetime.now(UTC) - timedelta(days=1),
                snippet="A transfer is scheduled.",
                capability_keys=["admin"],
                label_ids=["INBOX"],
            )
        )
        session.commit()

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("CAPABILITIES_PATH", str(CONFIG_PATH))
    clear_cache()
    engine_module.dispose_connection()

    yield TestClient(main.app)

    engine_module.dispose_connection()
    clear_cache()


def write(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post("/rules", headers=AUTH, json={**DRAFT, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def move(client: TestClient, rule_id: str, step: str, **body: Any) -> Any:
    return client.post(f"/rules/{rule_id}/{step}", headers=AUTH, json=body)


def through_to_active(client: TestClient) -> dict[str, Any]:
    rule = write(client)
    rule_id = rule["rule_id"]
    assert client.post(f"/rules/{rule_id}/test", headers=AUTH, json={}).status_code == 200
    assert move(client, rule_id, "confirm").status_code == 200
    activated = move(client, rule_id, "activate")
    assert activated.status_code == 200, activated.text
    return activated.json()


def test_the_rule_routes_require_authentication(client: TestClient) -> None:
    assert client.get("/rules").status_code == 401
    assert client.post("/rules", json=DRAFT).status_code == 401
    assert client.get("/rules/types").status_code == 401


def test_a_written_rule_is_proposed_and_does_nothing(client: TestClient) -> None:
    rule = write(client)

    assert rule["status"] == "proposed"
    assert rule["in_force"] is False
    assert rule["version"]["number"] == 1
    assert any("nothing happens to the mailbox" in line for line in rule["summary"])


def test_a_rule_that_matches_everything_is_refused_as_a_refusal(client: TestClient) -> None:
    response = client.post(
        "/rules",
        headers=AUTH,
        json={
            **DRAFT,
            "match": {"all": [{"field": "gmail_label", "operator": "equals", "value": "INBOX"}]},
        },
    )

    assert response.status_code == 409
    assert "narrow" in response.json()["detail"]


def test_a_rule_type_nothing_can_carry_out_is_refused(client: TestClient) -> None:
    response = client.post("/rules", headers=AUTH, json={**DRAFT, "rule_type": "todo_reminder_rule"})

    assert response.status_code == 422
    assert "schedule" in response.text


def test_testing_a_rule_shows_what_it_would_do_and_touches_nothing(
    client: TestClient,
) -> None:
    rule = write(client)

    response = client.post(f"/rules/{rule['rule_id']}/test", headers=AUTH, json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rule"]["status"] == "tested"
    assert body["report"]["executed"] is False
    assert body["report"]["counts"]["matched"] == 1
    matched = body["report"]["matched"][0]
    assert matched["captured"] == {"number": "207960765", "days": "3"}
    assert matched["requires_confirmation"] is True


def test_a_rule_can_be_tried_before_it_is_written_down(client: TestClient) -> None:
    response = client.post(
        "/rules/preview", headers=AUTH, json={"draft": DRAFT, "source": "current_snapshot"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["counts"]["matched"] == 1
    assert client.get("/rules", headers=AUTH).json()["rules"] == []


def test_confirming_without_testing_is_refused(client: TestClient) -> None:
    rule = write(client)

    response = move(client, rule["rule_id"], "confirm")

    assert response.status_code == 409
    assert "tested" in response.json()["detail"]


def test_confirming_is_not_activating(client: TestClient) -> None:
    rule = write(client)
    client.post(f"/rules/{rule['rule_id']}/test", headers=AUTH, json={})

    confirmed = move(client, rule["rule_id"], "confirm").json()

    assert confirmed["status"] == "confirmed"
    assert confirmed["in_force"] is False
    assert "active" in confirmed["next_statuses"]


def test_an_activated_rule_is_in_force(client: TestClient) -> None:
    activated = through_to_active(client)

    assert (activated["status"], activated["in_force"]) == ("active", True)
    assert activated["activated_at"] is not None


def test_a_paused_rule_can_be_resumed(client: TestClient) -> None:
    rule_id = through_to_active(client)["rule_id"]

    paused = move(client, rule_id, "pause").json()
    resumed = move(client, rule_id, "resume").json()

    assert (paused["in_force"], resumed["in_force"]) == (False, True)


def test_retiring_says_that_it_is_permanent(client: TestClient) -> None:
    rule_id = through_to_active(client)["rule_id"]

    refused = move(client, rule_id, "retire")

    assert refused.status_code == 400
    assert "cannot be brought back" in refused.json()["detail"]
    assert move(client, rule_id, "retire", confirm=True).json()["status"] == "retired"


def test_amending_writes_a_version_and_stands_the_rule_down(client: TestClient) -> None:
    rule_id = through_to_active(client)["rule_id"]

    amended = client.post(
        f"/rules/{rule_id}/versions",
        headers=AUTH,
        json={**DRAFT, "priority": 100, "change_reason": "Narrowed to one institution."},
    )

    assert amended.status_code == 200, amended.text
    assert amended.json()["status"] == "proposed"
    assert amended.json()["version"]["number"] == 2

    history = client.get(f"/rules/{rule_id}", headers=AUTH).json()
    assert [version["number"] for version in history["versions"]] == [1, 2]
    assert [event["kind"] for event in history["events"]] == [
        "proposed",
        "tested",
        "confirmed",
        "activated",
        "amended",
    ]


def test_rules_can_be_read_by_type_status_and_capability(client: TestClient) -> None:
    write(client)

    assert len(client.get("/rules", headers=AUTH).json()["rules"]) == 1
    assert client.get("/rules?status=active", headers=AUTH).json()["rules"] == []
    assert client.get("/rules?capability_key=other", headers=AUTH).json()["rules"] == []
    assert (
        len(client.get("/rules?rule_type=email_filing_rule", headers=AUTH).json()["rules"]) == 1
    )


def test_a_missing_rule_is_a_missing_rule(client: TestClient) -> None:
    assert client.get("/rules/nothing", headers=AUTH).status_code == 404
    assert move(client, "nothing", "confirm").status_code == 404


def test_the_fields_a_rule_may_match_on_are_published(client: TestClient) -> None:
    fields = client.get("/rules/fields", headers=AUTH).json()["fields"]

    keys = {field["key"] for field in fields}
    assert "snippet" in keys
    assert "body" not in keys
    assert {field["key"] for field in fields if not field["narrows"]} >= {"gmail_label"}


def test_a_rule_type_that_is_unavailable_says_why(client: TestClient) -> None:
    types = client.get("/rules/types", headers=AUTH).json()["rule_types"]

    unavailable = {
        entry["rule_type"]: entry["unavailable_because"]
        for entry in types
        if not entry["available"]
    }
    assert "schedule" in unavailable["todo_reminder_rule"]
    assert all(reason for reason in unavailable.values())


def test_a_subject_comes_back_as_readings_rather_than_a_rule(client: TestClient) -> None:
    response = client.post(
        "/rules/subject-readings", headers=AUTH, json={"subject": TRANSFER_SUBJECT}
    )

    assert response.status_code == 200, response.text
    readings = response.json()["readings"]
    assert [reading["key"] for reading in readings][:2] == ["exact", "pattern"]
    assert all(reading["catches"] and reading["misses"] for reading in readings)
    assert client.get("/rules", headers=AUTH).json()["rules"] == []
