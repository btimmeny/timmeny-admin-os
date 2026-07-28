import pytest

from adminos.config import (
    get_database_url,
    get_gmail_credentials,
    get_gmail_intake_label,
    is_gmail_write_enabled,
    normalize_database_url,
    redact_database_url,
)


GMAIL_VARIABLES = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")


@pytest.fixture
def no_gmail_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*GMAIL_VARIABLES, "GMAIL_INTAKE_LABEL", "GMAIL_WRITE_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def test_get_database_url_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert get_database_url() is None


def test_get_database_url_treats_blank_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "   ")

    assert get_database_url() is None


def test_get_database_url_strips_surrounding_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "  postgresql://user:pw@host:5432/db  ")

    assert get_database_url() == "postgresql://user:pw@host:5432/db"


@pytest.mark.parametrize(
    ("database_url", "expected"),
    [
        (
            "postgresql://user:pw@host:5432/db",
            "postgresql+psycopg://user:pw@host:5432/db",
        ),
        (
            "postgres://user:pw@host:5432/db",
            "postgresql+psycopg://user:pw@host:5432/db",
        ),
        (
            "postgresql+psycopg://user:pw@host:5432/db",
            "postgresql+psycopg://user:pw@host:5432/db",
        ),
        ("sqlite:////tmp/admin-os.db", "sqlite:////tmp/admin-os.db"),
    ],
)
def test_normalize_database_url(database_url: str, expected: str) -> None:
    assert normalize_database_url(database_url) == expected


def test_redact_database_url_removes_the_password() -> None:
    redacted = redact_database_url("postgresql://user:sup3rsecret@host:5432/db")

    assert redacted == "postgresql://user:***@host:5432/db"
    assert "sup3rsecret" not in redacted


def test_redact_database_url_leaves_credential_free_urls_alone() -> None:
    assert redact_database_url("sqlite:////tmp/admin-os.db") == "sqlite:////tmp/admin-os.db"


def test_gmail_credentials_are_none_when_unset(no_gmail_environment: None) -> None:
    assert get_gmail_credentials() is None


@pytest.mark.parametrize("provided", GMAIL_VARIABLES)
def test_partial_gmail_credentials_are_none(
    no_gmail_environment: None, monkeypatch: pytest.MonkeyPatch, provided: str
) -> None:
    """A half-populated environment must not authenticate as something unexpected."""
    monkeypatch.setenv(provided, "value")

    assert get_gmail_credentials() is None


def test_gmail_credentials_are_read_together(
    no_gmail_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in GMAIL_VARIABLES:
        monkeypatch.setenv(name, f"{name.lower()}-value")

    credentials = get_gmail_credentials()

    assert credentials is not None
    assert credentials.client_id == "gmail_client_id-value"
    assert credentials.refresh_token == "gmail_refresh_token-value"


def test_intake_label_defaults_to_the_configured_slice_label(
    no_gmail_environment: None,
) -> None:
    assert get_gmail_intake_label() == "financial/taxes"


def test_intake_label_can_be_overridden(
    no_gmail_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GMAIL_INTAKE_LABEL", "Receipts/2026")

    assert get_gmail_intake_label() == "Receipts/2026"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("TRUE", True), ("1", True), ("yes", True), ("false", False), ("", False)],
)
def test_gmail_write_enabled_parsing(
    no_gmail_environment: None, monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv("GMAIL_WRITE_ENABLED", value)

    assert is_gmail_write_enabled() is expected


def test_gmail_writes_are_disabled_by_default(no_gmail_environment: None) -> None:
    assert is_gmail_write_enabled() is False
