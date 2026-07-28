import os


DATABASE_URL_VARIABLE = "DATABASE_URL"


def get_database_url() -> str | None:
    value = os.getenv(DATABASE_URL_VARIABLE)
    if not value:
        return None
    return value.strip() or None


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
