from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import main
from adminos.db import engine as engine_module


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
API_KEY = "test-api-key"
HEAD_REVISION = "0014_guided_review"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    engine_module.dispose_connection()
    yield TestClient(main.app)
    engine_module.dispose_connection()


def migrate(url: str) -> None:
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def test_db_status_rejects_a_missing_key(client: TestClient) -> None:
    response = client.get("/admin/db-status")

    assert response.status_code == 401


def test_db_status_rejects_a_wrong_key(client: TestClient) -> None:
    response = client.get("/admin/db-status", headers={"X-API-Key": "nope"})

    assert response.status_code == 401


def test_db_status_fails_closed_when_no_key_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike the legacy todo routes, coordination endpoints must not fail open."""
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)

    response = client.get("/admin/db-status", headers={"X-API-Key": API_KEY})

    assert response.status_code == 503


def test_db_status_accepts_a_bearer_token(client: TestClient) -> None:
    response = client.get("/admin/db-status", headers={"Authorization": f"Bearer {API_KEY}"})

    assert response.status_code == 200


def test_db_status_reports_not_configured_without_a_database_url(client: TestClient) -> None:
    response = client.get("/admin/db-status", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"
    assert response.json()["revision"] is None


def test_db_status_reports_the_applied_revision(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'admin-os.db'}"
    migrate(url)
    monkeypatch.setenv("DATABASE_URL", url)

    response = client.get("/admin/db-status", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["detail"]) == ("ok", None)
    assert body["revision"] == HEAD_REVISION


def test_db_status_reports_an_unmigrated_database(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")

    response = client.get("/admin/db-status", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "migrations" in response.json()["detail"]


def test_db_status_reports_an_unreachable_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@127.0.0.1:1/absent")

    response = client.get("/admin/db-status", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "secret" not in response.text


def test_existing_routes_are_unaffected_without_a_database(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
