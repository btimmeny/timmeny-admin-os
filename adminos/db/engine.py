from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.config import (
    DATABASE_URL_VARIABLE,
    get_database_url,
    normalize_database_url,
)


class DatabaseNotConfigured(RuntimeError):
    """Raised when persistence is used without DATABASE_URL being set."""


@dataclass(frozen=True)
class Connection:
    database_url: str
    engine: Engine
    session_factory: sessionmaker[Session]


_connection: Connection | None = None


def is_configured() -> bool:
    return get_database_url() is not None


def get_connection() -> Connection:
    """Return the process-wide connection, rebuilding it if DATABASE_URL changed."""
    global _connection

    database_url = get_database_url()
    if database_url is None:
        raise DatabaseNotConfigured(
            f"{DATABASE_URL_VARIABLE} environment variable is not configured."
        )

    if _connection is None or _connection.database_url != database_url:
        dispose_connection()
        engine = create_engine(normalize_database_url(database_url), pool_pre_ping=True)
        _connection = Connection(
            database_url=database_url,
            engine=engine,
            session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        )

    return _connection


def get_engine() -> Engine:
    return get_connection().engine


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_connection().session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_connection() -> None:
    global _connection

    if _connection is not None:
        _connection.engine.dispose()
    _connection = None
