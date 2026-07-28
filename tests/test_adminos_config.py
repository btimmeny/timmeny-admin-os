import pytest

from adminos.config import (
    get_database_url,
    normalize_database_url,
    redact_database_url,
)


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
