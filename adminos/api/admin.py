from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from adminos.adapters.gmail import GmailAuthError, GmailError, open_gmail_client
from adminos.api.security import require_api_key
from adminos.config import (
    get_database_url,
    get_gmail_credentials,
    get_gmail_intake_label,
    is_gmail_write_enabled,
    redact_database_url,
)
from adminos.db import engine
from adminos.db.engine import DatabaseNotConfigured, session_scope
from adminos.domain.evidence import (
    DEFAULT_SYNC_LIMIT,
    MAX_SYNC_LIMIT,
    IntakeLabelMissing,
    sync_gmail_evidence,
)
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


class GmailStatusResponse(BaseModel):
    configured: bool
    intake_label: str
    intake_label_found: bool | None = None
    write_enabled: bool
    detail: str | None = None


class GmailSyncResponse(BaseModel):
    label: str
    scanned: int
    created: int
    updated: int
    unchanged: int


@router.get("/gmail/status", response_model=GmailStatusResponse)
async def read_gmail_status(_: None = Depends(require_api_key)) -> GmailStatusResponse:
    """Report whether Gmail is configured and the intake label resolves."""
    intake_label = get_gmail_intake_label()
    write_enabled = is_gmail_write_enabled()

    credentials = get_gmail_credentials()
    if credentials is None:
        return GmailStatusResponse(
            configured=False,
            intake_label=intake_label,
            write_enabled=write_enabled,
            detail=(
                "Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN "
                "to enable Gmail intake."
            ),
        )

    try:
        async with open_gmail_client(credentials) as client:
            label_id = await client.resolve_label_id(intake_label)
    except GmailError as exc:
        logger.error("gmail status check failed: %s", type(exc).__name__)
        return GmailStatusResponse(
            configured=True,
            intake_label=intake_label,
            write_enabled=write_enabled,
            detail=str(exc),
        )

    return GmailStatusResponse(
        configured=True,
        intake_label=intake_label,
        intake_label_found=label_id is not None,
        write_enabled=write_enabled,
        detail=None if label_id else f"Gmail has no label named {intake_label!r}.",
    )


@router.post("/gmail/sync", response_model=GmailSyncResponse)
async def sync_gmail(
    _: None = Depends(require_api_key),
    limit: int = Query(default=DEFAULT_SYNC_LIMIT, ge=1, le=MAX_SYNC_LIMIT),
) -> GmailSyncResponse:
    """Record threads carrying the intake label as evidence.

    Reads only. No Gmail labels change, no Monday task is created, and no
    classification happens here.
    """
    credentials = get_gmail_credentials()
    if credentials is None:
        raise HTTPException(status_code=503, detail="Gmail credentials are not configured.")

    try:
        async with open_gmail_client(credentials) as client:
            with session_scope() as session:
                result = await sync_gmail_evidence(
                    client,
                    session,
                    get_gmail_intake_label(),
                    limit=limit,
                )
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except IntakeLabelMissing as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GmailAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except GmailError as exc:
        logger.error("gmail sync failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return GmailSyncResponse(
        label=result.label,
        scanned=result.scanned,
        created=result.created,
        updated=result.updated,
        unchanged=result.unchanged,
    )


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
