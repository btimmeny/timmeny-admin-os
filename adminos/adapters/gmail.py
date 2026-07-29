import base64
import hashlib

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any, AsyncIterator, Sequence

import httpx

from adminos.config import GmailCredentials
from adminos.logging import get_logger


GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
INBOX_LABEL_ID = "INBOX"
TRASH_LABEL_ID = "TRASH"
SOURCE_SYSTEM = "gmail"
REQUEST_TIMEOUT_SECONDS = 20.0
TOKEN_EXPIRY_MARGIN_SECONDS = 60
MAX_SNIPPET_LENGTH = 500
MAX_PARTICIPANTS = 25
MAX_DRAFTS_SCANNED = 100
PARTICIPANT_HEADERS = ("From", "To", "Cc")
METADATA_HEADERS = ("Subject", "From", "To", "Cc", "Date")

logger = get_logger(__name__)


class GmailError(RuntimeError):
    """Raised when Gmail cannot be reached or returns an unusable response."""


class GmailAuthError(GmailError):
    """Raised when the stored refresh token no longer works."""


class GmailNotFound(GmailError):
    """Raised when Gmail says a thread, draft, or message does not exist.

    Separate from other failures so that absence can be treated as an answer:
    a draft that is gone has been sent or deleted, which is not the same as
    Gmail being unreachable.
    """


@dataclass(frozen=True)
class GmailDraft:
    """A draft Admin OS wrote, as Gmail reports it back.

    `message_id` is the draft's underlying message: sending is addressed by
    draft id, but the message id is what proves the draft that was approved is
    the draft that went out.
    """

    draft_id: str
    message_id: str | None
    thread_id: str | None


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

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """The only method that changes anything in the mailbox.

        Whether a write is permitted at all is decided before this is reached:
        the client enforces no policy, so the kill switch and the capability's
        permissions cannot be bypassed by calling it directly.
        """
        access_token = await self.get_access_token()
        response = await self._http_client.post(
            f"{GMAIL_API_BASE_URL}{path}",
            json=payload,
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

    async def list_thread_ids(self, label_ids: Sequence[str], limit: int) -> list[str]:
        """Return up to `limit` thread ids carrying *every* label, newest first.

        Gmail ANDs the ids, so passing INBOX alongside the intake label excludes
        archived threads that still carry the label.
        """
        thread_ids: list[str] = []
        page_token: str | None = None

        while len(thread_ids) < limit:
            params: dict[str, Any] = {
                "labelIds": list(label_ids),
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

    async def modify_thread(
        self,
        thread_id: str,
        add_label_ids: Sequence[str] = (),
        remove_label_ids: Sequence[str] = (),
    ) -> GmailThread:
        """Add and remove labels on every message in a thread.

        Naturally idempotent: Gmail treats adding a label a message already
        carries, or removing one it does not, as a no-op.
        """
        await self.post(
            f"/threads/{thread_id}/modify",
            {
                "addLabelIds": list(add_label_ids),
                "removeLabelIds": list(remove_label_ids),
            },
        )
        return await self.fetch_thread(thread_id)

    async def trash_thread(self, thread_id: str) -> GmailThread:
        """Move a whole thread to Gmail's Trash, where it stays recoverable.

        Not deletion: the thread keeps its messages and can be restored from
        Trash until Gmail empties it. The permanent operations Gmail offers,
        `threads.delete` and `messages.delete`, are deliberately not written
        here, so no caller can reach them.

        Idempotent in Gmail's own terms: trashing an already-trashed thread
        succeeds and leaves it where it is.
        """
        await self.post(f"/threads/{thread_id}/trash", {})
        return await self.fetch_thread(thread_id)

    async def untrash_thread(self, thread_id: str) -> GmailThread:
        """Take a whole thread back out of Trash, where trashing put it.

        The counterpart of `trash_thread`, and the reason trashing is safe to
        offer: a thread Gmail still holds can be returned to where it was.
        Idempotent in Gmail's own terms, like trashing.
        """
        await self.post(f"/threads/{thread_id}/untrash", {})
        return await self.fetch_thread(thread_id)

    async def create_draft(
        self,
        thread_id: str,
        to: Sequence[str],
        subject: str,
        body: str,
        cc: Sequence[str] = (),
    ) -> GmailDraft:
        """Create a reply draft on a thread. Never sends it."""
        payload = await self.post(
            "/drafts",
            {"message": {"threadId": thread_id, "raw": encode_message(to, subject, body, cc)}},
        )
        draft = build_draft(payload)
        if draft is None:
            raise GmailError("Gmail accepted the draft but did not say which draft it is.")
        return draft

    async def fetch_draft(self, draft_id: str) -> GmailDraft | None:
        """Read a draft back, or return None if it no longer exists."""
        try:
            payload = await self.request(f"/drafts/{draft_id}", params={"format": "metadata"})
        except GmailNotFound:
            return None
        return build_draft(payload)

    async def find_draft_for_thread(self, thread_id: str) -> GmailDraft | None:
        """Find an existing draft on a thread.

        Gmail has no idempotency token for draft creation, so this is what a
        retry uses to adopt the draft a timed-out attempt already created
        instead of writing a second one.
        """
        payload = await self.request("/drafts", params={"maxResults": MAX_DRAFTS_SCANNED})
        for entry in payload.get("drafts") or []:
            if not isinstance(entry, dict):
                continue
            draft = build_draft(entry)
            if draft is not None and draft.thread_id == thread_id:
                return draft
            if draft is not None and draft.thread_id is None:
                detailed = await self.fetch_draft(draft.draft_id)
                if detailed is not None and detailed.thread_id == thread_id:
                    return detailed
        return None

    async def send_draft(self, draft_id: str) -> str | None:
        """Send an existing draft, returning the id of the sent message."""
        payload = await self.post("/drafts/send", {"id": draft_id})
        message_id = payload.get("id")
        return message_id if isinstance(message_id, str) else None


@asynccontextmanager
async def open_gmail_client(credentials: GmailCredentials) -> AsyncIterator[GmailClient]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http_client:
        yield GmailClient(credentials, http_client)


def read_json(response: httpx.Response, source: str) -> dict[str, Any]:
    if response.status_code == 404:
        raise GmailNotFound(f"{source} says that does not exist.")
    if response.status_code >= 400:
        raise GmailError(f"{source} returned HTTP {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GmailError(f"{source} returned an invalid JSON response.") from exc
    if not isinstance(payload, dict):
        raise GmailError(f"{source} returned an unexpected response shape.")
    return payload


def encode_message(
    to: Sequence[str],
    subject: str,
    body: str,
    cc: Sequence[str] = (),
) -> str:
    message = EmailMessage()
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def build_draft(payload: dict[str, Any]) -> GmailDraft | None:
    draft_id = payload.get("id")
    if not isinstance(draft_id, str):
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return GmailDraft(draft_id=draft_id, message_id=None, thread_id=None)
    message_id = message.get("id")
    thread_id = message.get("threadId")
    return GmailDraft(
        draft_id=draft_id,
        message_id=message_id if isinstance(message_id, str) else None,
        thread_id=thread_id if isinstance(thread_id, str) else None,
    )


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
