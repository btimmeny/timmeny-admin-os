import hashlib

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses
from typing import Any, AsyncIterator

import httpx

from adminos.config import GmailCredentials
from adminos.logging import get_logger


GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
SOURCE_SYSTEM = "gmail"
REQUEST_TIMEOUT_SECONDS = 20.0
TOKEN_EXPIRY_MARGIN_SECONDS = 60
MAX_SNIPPET_LENGTH = 500
MAX_PARTICIPANTS = 25
PARTICIPANT_HEADERS = ("From", "To", "Cc")
METADATA_HEADERS = ("Subject", "From", "To", "Cc", "Date")

logger = get_logger(__name__)


class GmailError(RuntimeError):
    """Raised when Gmail cannot be reached or returns an unusable response."""


class GmailAuthError(GmailError):
    """Raised when the stored refresh token no longer works."""


@dataclass(frozen=True)
class GmailThread:
    """The metadata Admin OS retains for one Gmail thread.

    Deliberately excludes message bodies and attachments; see ADR-0003.
    """

    thread_id: str
    message_id: str | None
    subject: str | None
    participants: list[str]
    received_at: datetime | None
    snippet: str | None
    label_ids: list[str] = field(default_factory=list)

    def content_hash(self) -> str:
        """A digest of the fields that decide whether evidence has changed."""
        parts = [
            self.thread_id,
            self.message_id or "",
            self.subject or "",
            ",".join(self.participants),
            self.received_at.isoformat() if self.received_at else "",
            self.snippet or "",
        ]
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class GmailClient:
    """A minimal Gmail REST client.

    Only the calls the slice needs are implemented, and every message read uses
    `format=metadata`, so bodies and attachments never cross the wire.
    """

    def __init__(self, credentials: GmailCredentials, http_client: httpx.AsyncClient) -> None:
        self._credentials = credentials
        self._http_client = http_client
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None

    async def get_access_token(self) -> str:
        """Return a cached access token, refreshing it when close to expiry."""
        now = datetime.now(UTC)
        if (
            self._access_token is not None
            and self._access_token_expires_at is not None
            and now < self._access_token_expires_at
        ):
            return self._access_token

        response = await self._http_client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": self._credentials.client_id,
                "client_secret": self._credentials.client_secret,
                "refresh_token": self._credentials.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code in {400, 401}:
            raise GmailAuthError(
                "Gmail rejected the refresh token. It may have been revoked, or the "
                "OAuth consent screen may still be in Testing status, which expires "
                "refresh tokens after seven days."
            )
        payload = read_json(response, "the Google token endpoint")

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GmailAuthError("Google did not return an access token.")

        expires_in = payload.get("expires_in")
        lifetime = expires_in if isinstance(expires_in, int) else 0
        self._access_token = access_token
        self._access_token_expires_at = now + timedelta(
            seconds=max(lifetime - TOKEN_EXPIRY_MARGIN_SECONDS, 0)
        )
        return access_token

    async def request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        access_token = await self.get_access_token()
        response = await self._http_client.get(
            f"{GMAIL_API_BASE_URL}{path}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code == 401:
            raise GmailAuthError("Gmail rejected the access token.")
        return read_json(response, "Gmail")

    async def resolve_label_id(self, label_name: str) -> str | None:
        """Return the id of a label, matched case-insensitively by name."""
        payload = await self.request("/labels")
        wanted = label_name.casefold()
        for label in payload.get("labels") or []:
            if not isinstance(label, dict):
                continue
            name = label.get("name")
            label_id = label.get("id")
            if isinstance(name, str) and isinstance(label_id, str) and name.casefold() == wanted:
                return label_id
        return None

    async def list_thread_ids(self, label_id: str, limit: int) -> list[str]:
        """Return up to `limit` thread ids carrying the label, newest first."""
        thread_ids: list[str] = []
        page_token: str | None = None

        while len(thread_ids) < limit:
            params: dict[str, Any] = {
                "labelIds": label_id,
                "maxResults": min(limit - len(thread_ids), 100),
            }
            if page_token:
                params["pageToken"] = page_token

            payload = await self.request("/threads", params=params)
            for thread in payload.get("threads") or []:
                if isinstance(thread, dict) and isinstance(thread.get("id"), str):
                    thread_ids.append(thread["id"])

            next_page_token = payload.get("nextPageToken")
            if not isinstance(next_page_token, str) or not next_page_token:
                break
            page_token = next_page_token

        return thread_ids[:limit]

    async def fetch_thread(self, thread_id: str) -> GmailThread:
        payload = await self.request(
            f"/threads/{thread_id}",
            params={"format": "metadata", "metadataHeaders": list(METADATA_HEADERS)},
        )
        return build_thread(thread_id, payload)


@asynccontextmanager
async def open_gmail_client(credentials: GmailCredentials) -> AsyncIterator[GmailClient]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http_client:
        yield GmailClient(credentials, http_client)


def read_json(response: httpx.Response, source: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise GmailError(f"{source} returned HTTP {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GmailError(f"{source} returned an invalid JSON response.") from exc
    if not isinstance(payload, dict):
        raise GmailError(f"{source} returned an unexpected response shape.")
    return payload


def build_thread(thread_id: str, payload: dict[str, Any]) -> GmailThread:
    messages = [message for message in payload.get("messages") or [] if isinstance(message, dict)]
    if not messages:
        return GmailThread(
            thread_id=thread_id,
            message_id=None,
            subject=None,
            participants=[],
            received_at=None,
            snippet=None,
        )

    first_message = messages[0]
    latest_message = messages[-1]
    headers = read_headers(latest_message)

    return GmailThread(
        thread_id=thread_id,
        message_id=(
            latest_message.get("id") if isinstance(latest_message.get("id"), str) else None
        ),
        subject=read_headers(first_message).get("subject") or headers.get("subject"),
        participants=collect_participants(messages),
        received_at=read_internal_date(latest_message),
        snippet=read_snippet(latest_message),
        label_ids=collect_label_ids(messages),
    )


def read_headers(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return {}
    headers: dict[str, str] = {}
    for header in payload.get("headers") or []:
        if not isinstance(header, dict):
            continue
        name = header.get("name")
        value = header.get("value")
        if isinstance(name, str) and isinstance(value, str):
            headers[name.casefold()] = value
    return headers


def collect_participants(messages: list[dict[str, Any]]) -> list[str]:
    """Return the unique addresses on the thread, without display names."""
    addresses: list[str] = []
    seen: set[str] = set()
    for message in messages:
        headers = read_headers(message)
        raw_values = [headers.get(name.casefold(), "") for name in PARTICIPANT_HEADERS]
        for _display_name, address in getaddresses(raw_values):
            normalized = address.strip().casefold()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            addresses.append(normalized)
            if len(addresses) == MAX_PARTICIPANTS:
                return addresses
    return addresses


def collect_label_ids(messages: list[dict[str, Any]]) -> list[str]:
    label_ids: list[str] = []
    for message in messages:
        for label_id in message.get("labelIds") or []:
            if isinstance(label_id, str) and label_id not in label_ids:
                label_ids.append(label_id)
    return label_ids


def read_internal_date(message: dict[str, Any]) -> datetime | None:
    internal_date = message.get("internalDate")
    if not isinstance(internal_date, (str, int)):
        return None
    try:
        milliseconds = int(internal_date)
    except ValueError:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def read_snippet(message: dict[str, Any]) -> str | None:
    snippet = message.get("snippet")
    if not isinstance(snippet, str):
        return None
    stripped = snippet.strip()
    if not stripped:
        return None
    return stripped[:MAX_SNIPPET_LENGTH]
