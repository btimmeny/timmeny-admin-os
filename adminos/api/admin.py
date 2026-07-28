from enum import StrEnum

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from adminos.api.security import require_api_key
from adminos.config import get_database_url, redact_database_url
from adminos.db import engine
from adminos.logging import get_logger


logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


class DatabaseStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    OK = "ok"
    ERROR = "error"


class DatabaseStatusResponse(BaseModel):
    status: DatabaseStatus
    revision: str | None = None
    detail: str | None = None


@router.get("/db-status", response_model=DatabaseStatusResponse)
def read_database_status(_: None = Depends(require_api_key)) -> DatabaseStatusResponse:
    """Report whether the operational database is reachable and migrated."""
    database_url = get_database_url()
    if database_url is None:
        return DatabaseStatusResponse(
            status=DatabaseStatus.NOT_CONFIGURED,
            detail="DATABASE_URL is not set; Admin OS is running without persistence.",
        )

    try:
        revision = read_current_revision()
    except SQLAlchemyError as exc:
        logger.error(
            "database status check failed for %s: %s",
            redact_database_url(database_url),
            type(exc).__name__,
        )
        return DatabaseStatusResponse(
            status=DatabaseStatus.ERROR,
            detail="Could not query the database. See service logs.",
        )

    if revision is None:
        return DatabaseStatusResponse(
            status=DatabaseStatus.ERROR,
            detail=(
                "Database is reachable but no migrations have been applied. "
                "Run 'alembic upgrade head'."
            ),
        )

    return DatabaseStatusResponse(status=DatabaseStatus.OK, revision=revision)


def read_current_revision() -> str | None:
    """Return the applied Alembic revision, or None if the schema is absent."""
    with engine.get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
        if not inspect(connection).has_table("alembic_version"):
            return None
        row = connection.execute(text("SELECT version_num FROM alembic_version")).first()

    if row is None:
        return None
    return str(row[0])
