import os

from dataclasses import dataclass


DATABASE_URL_VARIABLE = "DATABASE_URL"
GMAIL_CLIENT_ID_VARIABLE = "GMAIL_CLIENT_ID"
GMAIL_CLIENT_SECRET_VARIABLE = "GMAIL_CLIENT_SECRET"
GMAIL_REFRESH_TOKEN_VARIABLE = "GMAIL_REFRESH_TOKEN"
GMAIL_INTAKE_LABEL_VARIABLE = "GMAIL_INTAKE_LABEL"
GMAIL_WRITE_ENABLED_VARIABLE = "GMAIL_WRITE_ENABLED"
DEFAULT_GMAIL_INTAKE_LABEL = "financial/taxes"
TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class GmailCredentials:
    client_id: str
    client_secret: str
    refresh_token: str


def get_optional_setting(name: str) -> str | None:
    value = os.getenv(name)
    if not value:
        return None
    return value.strip() or None


def get_database_url() -> str | None:
    return get_optional_setting(DATABASE_URL_VARIABLE)


def get_gmail_credentials() -> GmailCredentials | None:
    """Return the Gmail OAuth credentials, or None if any part is missing.

    Partial configuration is treated as unconfigured rather than as an error so
    that a half-populated environment cannot silently authenticate as something
    unexpected.
    """
    client_id = get_optional_setting(GMAIL_CLIENT_ID_VARIABLE)
    client_secret = get_optional_setting(GMAIL_CLIENT_SECRET_VARIABLE)
    refresh_token = get_optional_setting(GMAIL_REFRESH_TOKEN_VARIABLE)
    if not (client_id and client_secret and refresh_token):
        return None
    return GmailCredentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )


def get_gmail_intake_label() -> str:
    return get_optional_setting(GMAIL_INTAKE_LABEL_VARIABLE) or DEFAULT_GMAIL_INTAKE_LABEL


def is_gmail_write_enabled() -> bool:
    """Gmail writes stay off unless explicitly enabled; see ADR-0003."""
    value = get_optional_setting(GMAIL_WRITE_ENABLED_VARIABLE)
    if value is None:
        return False
    return value.casefold() in TRUE_VALUES


def normalize_database_url(database_url: str) -> str:
    """Return a URL that resolves to the psycopg 3 driver.

    Railway publishes `postgresql://` and some providers still publish the
    legacy `postgres://`. Both resolve to psycopg 2 under SQLAlchemy, which is
    not installed.
    """
    remainder = strip_prefix(database_url, "postgres://")
    if remainder is None:
        remainder = strip_prefix(database_url, "postgresql://")
    if remainder is None:
        return database_url
    return f"postgresql+psycopg://{remainder}"


def strip_prefix(value: str, prefix: str) -> str | None:
    if not value.startswith(prefix):
        return None
    return value[len(prefix) :]


def redact_database_url(database_url: str) -> str:
    """Return the URL with any password removed, safe to log."""
    scheme, separator, remainder = database_url.partition("://")
    if not separator:
        return database_url

    credentials, at_sign, host = remainder.rpartition("@")
    if not at_sign:
        return database_url

    user, colon, _password = credentials.partition(":")
    if not colon:
        return database_url

    return f"{scheme}://{user}:***@{host}"
