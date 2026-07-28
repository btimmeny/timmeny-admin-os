from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from adminos.db import engine as engine_module
from adminos.db.models import Evidence


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    engine_module.dispose_connection()
    yield TestClient(main.app)
    engine_module.dispose_connection()


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'classification-api.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    monkeypatch.setenv("DATABASE_URL", url)
    engine_module.dispose_connection()
    return url


def add_evidence(url: str, thread_id: str, subject: str) -> None:
    factory = sessionmaker(bind=create_engine(url))
    with factory() as session:
        session.add(
            Evidence(
                source_system="gmail",
                source_thread_id=thread_id,
                subject=subject,
                participants=["cpa@example.com"],
                received_at=datetime(2026, 3, 1, tzinfo=UTC),
                content_hash=f"hash-{thread_id}",
            )
        )
        session.commit()


def test_classify_requires_a_key(client: TestClient) -> None:
    assert client.post("/admin/classify").status_code == 401


def test_review_queue_requires_a_key(client: TestClient) -> None:
    assert client.get("/admin/review-queue").status_code == 401


def test_classify_reports_a_missing_database(client: TestClient) -> None:
    assert client.post("/admin/classify", headers=AUTH).status_code == 503


def test_review_queue_reports_a_missing_database(client: TestClient) -> None:
    assert client.get("/admin/review-queue", headers=AUTH).status_code == 503


def test_classify_populates_the_review_queue(client: TestClient, database: str) -> None:
    add_evidence(database, "t1", "Q3 estimate")

    classified = client.post("/admin/classify", headers=AUTH).json()
    queue = client.get("/admin/review-queue", headers=AUTH).json()

    assert (classified["scanned"], classified["created"]) == (1, 1)
    assert classified["classifier_version"] == "v1-review-all"
    assert queue["count"] == 1
    assert queue["items"][0]["source_thread_id"] == "t1"
    assert queue["items"][0]["disposition"] == "needs_review"


def test_repeated_classify_creates_nothing_new(client: TestClient, database: str) -> None:
    add_evidence(database, "t1", "Q3 estimate")
    client.post("/admin/classify", headers=AUTH)

    body = client.post("/admin/classify", headers=AUTH).json()

    assert (body["created"], body["unchanged"]) == (0, 1)
    assert client.get("/admin/review-queue", headers=AUTH).json()["count"] == 1


def test_review_queue_is_empty_before_classification(client: TestClient, database: str) -> None:
    add_evidence(database, "t1", "Q3 estimate")

    body = client.get("/admin/review-queue", headers=AUTH).json()

    assert body == {"count": 0, "items": []}


def test_review_queue_rejects_an_out_of_range_limit(client: TestClient, database: str) -> None:
    assert client.get("/admin/review-queue?limit=0", headers=AUTH).status_code == 422
    assert client.get("/admin/review-queue?limit=500", headers=AUTH).status_code == 422
