from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Sequence

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import main
from adminos.adapters.gmail import GmailAuthError, GmailNotFound, GmailThread
from adminos.capabilities.config import clear_cache
from adminos.api import admin as admin_module
from adminos.db import engine as engine_module


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}


class FakeGmailClient:
    def __init__(
        self,
        labels: dict[str, str] | None = None,
        threads: dict[str, GmailThread] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.labels = labels or {}
        self.threads = threads or {}
        self.error = error

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
        return list(self.threads)[:limit]

    async def fetch_thread(self, thread_id: str) -> GmailThread:
        thread = self.threads.get(thread_id)
        if thread is None:
            raise GmailNotFound(f"No thread {thread_id!r}.")
        return thread


def install_client(monkeypatch: pytest.MonkeyPatch, fake: FakeGmailClient) -> None:
    @asynccontextmanager
    async def open_client(_credentials: object) -> AsyncIterator[FakeGmailClient]:
        yield fake

    monkeypatch.setattr(admin_module, "open_gmail_client", open_client)


def configure_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh-token")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("CAPABILITIES_PATH", str(REPOSITORY_ROOT / "tests/data/capabilities_single.yaml"))
    clear_cache()
    for name in ("DATABASE_URL", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("GMAIL_WRITE_ENABLED", raising=False)
    engine_module.dispose_connection()
    yield TestClient(main.app)
    engine_module.dispose_connection()


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'admin-os.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    monkeypatch.setenv("DATABASE_URL", url)
    engine_module.dispose_connection()
    return url


def thread(thread_id: str, subject: str) -> GmailThread:
    return GmailThread(
        thread_id=thread_id,
        message_id="m1",
        subject=subject,
        participants=["cpa@example.com"],
        received_at=None,
        snippet="Attached is the estimate.",
        label_ids=["INBOX", "Label_9"],
    )


def test_gmail_status_requires_authentication(client: TestClient) -> None:
    assert client.get("/admin/gmail/status").status_code == 401


def test_gmail_sync_requires_authentication(client: TestClient) -> None:
    assert client.post("/admin/gmail/sync").status_code == 401


def test_gmail_status_reports_unconfigured_credentials(client: TestClient) -> None:
    response = client.get("/admin/gmail/status", headers=AUTH)

    body = response.json()
    assert response.status_code == 200
    assert body["configured"] is False
    assert body["labels"] == [
        {"capability_key": "financial_taxes", "label": "financial/taxes", "found": None}
    ]
    assert body["write_enabled"] is False


def test_gmail_status_reports_a_resolved_label(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_credentials(monkeypatch)
    install_client(monkeypatch, FakeGmailClient(labels={"financial/taxes": "Label_9"}))

    body = client.get("/admin/gmail/status", headers=AUTH).json()

    assert body["configured"] is True
    assert body["labels"][0]["found"] is True
    assert body["detail"] is None


def test_gmail_status_reports_a_missing_label(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_credentials(monkeypatch)
    install_client(monkeypatch, FakeGmailClient(labels={}))

    body = client.get("/admin/gmail/status", headers=AUTH).json()

    assert body["labels"][0]["found"] is False
    assert "financial/taxes" in body["detail"]


def test_gmail_status_surfaces_an_auth_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_credentials(monkeypatch)
    install_client(monkeypatch, FakeGmailClient(error=GmailAuthError("token rejected")))

    body = client.get("/admin/gmail/status", headers=AUTH).json()

    assert body["configured"] is True
    assert body["labels"][0]["found"] is None
    assert body["detail"] == "token rejected"


def test_partial_credentials_count_as_unconfigured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")

    assert client.get("/admin/gmail/status", headers=AUTH).json()["configured"] is False


def test_gmail_sync_requires_credentials(client: TestClient) -> None:
    response = client.post("/admin/gmail/sync", headers=AUTH)

    assert response.status_code == 503
    assert response.json()["detail"] == "Gmail credentials are not configured."


def test_gmail_sync_requires_a_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_credentials(monkeypatch)
    install_client(
        monkeypatch,
        FakeGmailClient(labels={"financial/taxes": "Label_9"}, threads={"t1": thread("t1", "s")}),
    )

    response = client.post("/admin/gmail/sync", headers=AUTH)

    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]


def test_gmail_sync_records_evidence(
    client: TestClient, database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_credentials(monkeypatch)
    install_client(
        monkeypatch,
        FakeGmailClient(
            labels={"financial/taxes": "Label_9"},
            threads={"t1": thread("t1", "Q3 estimate"), "t2": thread("t2", "1099")},
        ),
    )

    response = client.post("/admin/gmail/sync", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "scope": "inbox",
        "labels": ["financial/taxes"],
        "scanned": 2,
        "created": 2,
        "updated": 0,
        "unchanged": 0,
        "removed": 0,
        "warnings": [],
    }


def test_repeated_sync_creates_nothing_new(
    client: TestClient, database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_credentials(monkeypatch)
    install_client(
        monkeypatch,
        FakeGmailClient(
            labels={"financial/taxes": "Label_9"}, threads={"t1": thread("t1", "Q3 estimate")}
        ),
    )

    client.post("/admin/gmail/sync", headers=AUTH)
    body = client.post("/admin/gmail/sync", headers=AUTH).json()

    assert (body["created"], body["unchanged"]) == (0, 1)


def test_gmail_sync_warns_about_a_missing_intake_label(
    client: TestClient, database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A label that does not exist is reported, not fatal: other capabilities still sync."""
    configure_credentials(monkeypatch)
    install_client(monkeypatch, FakeGmailClient(labels={}))

    response = client.post("/admin/gmail/sync", headers=AUTH)

    assert response.status_code == 200
    assert "financial/taxes" in response.json()["warnings"][0]


def test_gmail_sync_prunes_only_when_asked(
    client: TestClient, database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_credentials(monkeypatch)
    install_client(
        monkeypatch,
        FakeGmailClient(
            labels={"financial/taxes": "Label_9"},
            threads={"t1": thread("t1", "Q3 estimate"), "t2": thread("t2", "1099")},
        ),
    )
    client.post("/admin/gmail/sync", headers=AUTH)

    install_client(
        monkeypatch,
        FakeGmailClient(
            labels={"financial/taxes": "Label_9"}, threads={"t1": thread("t1", "Q3 estimate")}
        ),
    )

    assert client.post("/admin/gmail/sync", headers=AUTH).json()["removed"] == 0
    assert client.post("/admin/gmail/sync?prune=true", headers=AUTH).json()["removed"] == 1


def test_gmail_sync_refuses_to_prune_a_truncated_scan(
    client: TestClient, database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_credentials(monkeypatch)
    install_client(
        monkeypatch,
        FakeGmailClient(
            labels={"financial/taxes": "Label_9"},
            threads={"t1": thread("t1", "Q3 estimate"), "t2": thread("t2", "1099")},
        ),
    )

    response = client.post("/admin/gmail/sync?limit=1&prune=true", headers=AUTH)

    assert response.status_code == 409


def test_gmail_sync_rejects_an_out_of_range_limit(
    client: TestClient, database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_credentials(monkeypatch)
    install_client(monkeypatch, FakeGmailClient(labels={"financial/taxes": "Label_9"}))

    assert client.post("/admin/gmail/sync?limit=0", headers=AUTH).status_code == 422
    assert client.post("/admin/gmail/sync?limit=500", headers=AUTH).status_code == 422


def test_write_enabled_defaults_off_and_parses_true(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/admin/gmail/status", headers=AUTH).json()["write_enabled"] is False

    monkeypatch.setenv("GMAIL_WRITE_ENABLED", "true")

    assert client.get("/admin/gmail/status", headers=AUTH).json()["write_enabled"] is True
