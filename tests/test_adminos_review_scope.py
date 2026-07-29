"""What a review looks at, and what it refuses to look at.

The mailbox here is not a list of threads to hand back: it is a small
imitation of Gmail that keeps labels, honours `labelIds`, and understands the
handful of search operators Admin OS uses. A test can therefore archive a
thread, snooze it, or bin it, and see what a review does about it — rather
than assert that some Python filter was called.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Sequence

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import main
from adminos.adapters.gmail import (
    DRAFT_LABEL_ID,
    INBOX_LABEL_ID,
    SENT_LABEL_ID,
    SPAM_LABEL_ID,
    TRASH_LABEL_ID,
    GmailNotFound,
    GmailThread,
)
from adminos.api import review as review_module
from adminos.capabilities.config import clear_cache
from adminos.db import engine as engine_module


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPOSITORY_ROOT / "tests/data/capabilities_pair.yaml"
API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}
TAXES_LABEL_ID = "Label_9"
ADMIN_LABEL_ID = "Label_4"


class FakeMailbox:
    """Gmail, as far as scope is concerned: labels, a search, and snoozing.

    Snoozing is modelled separately from labels because that is how Gmail
    behaves. A snoozed thread keeps its INBOX label and the API publishes no
    label saying it is asleep, so `in:snoozed` is the only way to ask.
    """

    def __init__(self) -> None:
        self.labels = {"financial/taxes": TAXES_LABEL_ID, "Admin": ADMIN_LABEL_ID}
        self.threads: dict[str, GmailThread] = {}
        self.snoozed: set[str] = set()
        self.queries: list[str | None] = []
        self.listed_labels: list[list[str]] = []

    def add(
        self,
        thread_id: str,
        subject: str,
        label_ids: Sequence[str] = (INBOX_LABEL_ID, TAXES_LABEL_ID),
        snoozed: bool = False,
    ) -> None:
        self.threads[thread_id] = GmailThread(
            thread_id=thread_id,
            message_id=f"m-{thread_id}",
            subject=subject,
            participants=["cpa@kpmg.com"],
            received_at=datetime(2026, 7, 20, tzinfo=UTC),
            snippet="Attached is the estimate.",
            label_ids=list(label_ids),
        )
        if snoozed:
            self.snoozed.add(thread_id)

    def relabel(self, thread_id: str, label_ids: Sequence[str]) -> None:
        """Move a thread, as archiving or binning it in Gmail would."""
        existing = self.threads[thread_id]
        self.threads[thread_id] = GmailThread(
            thread_id=existing.thread_id,
            message_id=existing.message_id,
            subject=existing.subject,
            participants=list(existing.participants),
            received_at=existing.received_at,
            snippet=existing.snippet,
            label_ids=list(label_ids),
        )

    async def resolve_label_id(self, label_name: str) -> str | None:
        return self.labels.get(label_name)

    async def list_thread_ids(
        self,
        label_ids: Sequence[str],
        limit: int,
        query: str | None = None,
    ) -> list[str]:
        self.listed_labels.append(list(label_ids))
        self.queries.append(query)
        terms = (query or "").split()
        found = [
            thread_id
            for thread_id, thread in self.threads.items()
            if set(label_ids) <= set(thread.label_ids) and self.matches(thread_id, terms)
        ]
        return found[:limit]

    def matches(self, thread_id: str, terms: Sequence[str]) -> bool:
        labels = set(self.threads[thread_id].label_ids)
        anywhere = "in:anywhere" in terms
        if not anywhere and (TRASH_LABEL_ID in labels or SPAM_LABEL_ID in labels):
            return False
        if "-in:snoozed" in terms and thread_id in self.snoozed:
            return False
        if "in:snoozed" in terms and thread_id not in self.snoozed:
            return False
        if "-in:inbox" in terms and INBOX_LABEL_ID in labels:
            return False
        return True

    async def fetch_thread(self, thread_id: str) -> GmailThread:
        thread = self.threads.get(thread_id)
        if thread is None:
            raise GmailNotFound(f"No thread {thread_id!r}.")
        return thread


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch) -> FakeMailbox:
    fake = FakeMailbox()

    @asynccontextmanager
    async def open_client(_credentials: object) -> AsyncIterator[FakeMailbox]:
        yield fake

    monkeypatch.setattr(review_module, "open_gmail_client", open_client)
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh-token")
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'review-scope.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("CAPABILITIES_PATH", str(CONFIG_PATH))
    clear_cache()
    engine_module.dispose_connection()

    yield TestClient(main.app)

    engine_module.dispose_connection()
    clear_cache()


def start(client: TestClient, **body: Any) -> dict[str, Any]:
    """Open today's review and begin its plan.

    A review states its plan before it presents a group, so getting to a table
    takes the two calls a morning takes.
    """
    response = client.post("/review/start", headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    return begin(client, response.json()["run_id"])


def begin(client: TestClient, run_id: str, **plan: Any) -> dict[str, Any]:
    response = client.post(f"/review/runs/{run_id}/plan", headers=AUTH, json=plan)
    assert response.status_code == 200, response.text
    return response.json()


def resume(client: TestClient, **body: Any) -> dict[str, Any]:
    """Pick the review back up, which is what reads the mailbox into it again."""
    response = client.post("/review/continue", headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def group_of(client: TestClient, run_id: str, capability_key: str) -> dict[str, Any]:
    response = client.get(f"/review/runs/{run_id}/groups/{capability_key}", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def threads_in(client: TestClient, run_id: str, capability_key: str = "financial_taxes") -> set[str]:
    group = group_of(client, run_id, capability_key)
    return {item["thread_id"] for item in group["items"] if item["state"] == "pending"}


def test_the_default_review_asks_gmail_for_the_inbox(
    client: TestClient, gmail: FakeMailbox
) -> None:
    """The scope is the query. Nothing downstream has to filter it back out."""
    gmail.add("t1", "Q3 estimate")

    start(client)

    assert [INBOX_LABEL_ID, TAXES_LABEL_ID] in gmail.listed_labels
    assert gmail.queries[0] == "-in:snoozed"


def test_an_archived_thread_is_not_in_the_default_review(
    client: TestClient, gmail: FakeMailbox
) -> None:
    gmail.add("kept", "Q3 estimate")
    gmail.add("archived", "Settled last year", label_ids=[TAXES_LABEL_ID])

    run = start(client)

    assert threads_in(client, run["run_id"]) == {"kept"}


def test_a_snoozed_thread_is_not_in_the_default_review(
    client: TestClient, gmail: FakeMailbox
) -> None:
    """Snoozing keeps the INBOX label, so only the query can exclude it."""
    gmail.add("kept", "Q3 estimate")
    gmail.add("asleep", "Chase this in a fortnight", snoozed=True)

    run = start(client)

    assert threads_in(client, run["run_id"]) == {"kept"}


def test_a_trashed_thread_is_not_in_the_default_review(
    client: TestClient, gmail: FakeMailbox
) -> None:
    gmail.add("kept", "Q3 estimate")
    gmail.add("binned", "Old receipt", label_ids=[TRASH_LABEL_ID, TAXES_LABEL_ID])

    run = start(client)

    assert threads_in(client, run["run_id"]) == {"kept"}


def test_spam_is_not_in_the_default_review(client: TestClient, gmail: FakeMailbox) -> None:
    gmail.add("kept", "Q3 estimate")
    gmail.add("junk", "You have won", label_ids=[SPAM_LABEL_ID, TAXES_LABEL_ID])

    run = start(client)

    assert threads_in(client, run["run_id"]) == {"kept"}


def test_sent_mail_is_not_in_the_default_review(client: TestClient, gmail: FakeMailbox) -> None:
    gmail.add("kept", "Q3 estimate")
    gmail.add("outgoing", "Here are the figures", label_ids=[SENT_LABEL_ID, TAXES_LABEL_ID])

    run = start(client)

    assert threads_in(client, run["run_id"]) == {"kept"}


def test_a_draft_is_not_in_the_default_review(client: TestClient, gmail: FakeMailbox) -> None:
    gmail.add("kept", "Q3 estimate")
    gmail.add("unsent", "Half a reply", label_ids=[DRAFT_LABEL_ID, TAXES_LABEL_ID])

    run = start(client)

    assert threads_in(client, run["run_id"]) == {"kept"}


def test_a_conversation_replied_to_is_still_in_the_review(
    client: TestClient, gmail: FakeMailbox
) -> None:
    """A thread's labels are the union of its messages'.

    Replying puts SENT on a thread that is still in the inbox and still needs
    answering. Excluding everything carrying SENT would quietly drop exactly
    the conversations Brian is most involved in.
    """
    gmail.add(
        "replied",
        "Q3 estimate",
        label_ids=[INBOX_LABEL_ID, SENT_LABEL_ID, TAXES_LABEL_ID],
    )

    run = start(client)

    assert threads_in(client, run["run_id"]) == {"replied"}


def test_losing_the_inbox_label_takes_a_thread_out_of_the_review(
    client: TestClient, gmail: FakeMailbox
) -> None:
    """Archiving in Gmail is an answer, and the review must not ask again."""
    gmail.add("t1", "Q3 estimate")
    gmail.add("t2", "1099 from broker")
    first = start(client)
    assert threads_in(client, first["run_id"]) == {"t1", "t2"}

    gmail.relabel("t2", [TAXES_LABEL_ID])
    resumed = resume(client)

    assert resumed["run_id"] == first["run_id"]
    assert threads_in(client, resumed["run_id"]) == {"t1"}


def test_a_thread_archived_before_a_fresh_snapshot_is_not_in_it(
    client: TestClient, gmail: FakeMailbox
) -> None:
    """An inbox review is of what carries the label now, not of what did."""
    gmail.add("t1", "Q3 estimate")
    gmail.add("t2", "1099 from broker")
    first = start(client)
    gmail.relabel("t2", [TAXES_LABEL_ID])

    second = start(client)

    assert second["run_id"] != first["run_id"]
    assert threads_in(client, second["run_id"]) == {"t1"}


def test_a_withdrawn_row_is_still_on_the_record(client: TestClient, gmail: FakeMailbox) -> None:
    """Leaving the scope settles nothing, so the row is deferred, not deleted."""
    gmail.add("t1", "Q3 estimate")
    run = start(client)
    gmail.relabel("t1", [TAXES_LABEL_ID])

    resume(client)

    group = group_of(client, run["run_id"], "financial_taxes")
    states = {item["thread_id"]: item["state"] for item in group["items"]}
    assert states == {"t1": "deferred"}


def test_a_thread_deleted_in_gmail_leaves_the_review(
    client: TestClient, gmail: FakeMailbox
) -> None:
    gmail.add("t1", "Q3 estimate")
    run = start(client)
    del gmail.threads["t1"]

    resume(client)

    assert threads_in(client, run["run_id"]) == set()


def test_the_review_says_what_it_looked_at(client: TestClient, gmail: FakeMailbox) -> None:
    """The GPT should never have to infer the scope from what came back."""
    gmail.add("t1", "Q3 estimate")

    run = start(client)

    assert run["scope"] == {
        "name": "inbox",
        "mailbox": "INBOX",
        "include_snoozed": False,
        "include_archived": False,
        "include_trash": False,
        "include_spam": False,
        "include_sent": False,
        "include_drafts": False,
        "requested": False,
        "gmail_query": "-in:snoozed",
        "description": run["scope"]["description"],
    }
    assert "inbox" in run["scope"]["description"]


def test_each_group_reports_the_scope_it_was_built_from(
    client: TestClient, gmail: FakeMailbox
) -> None:
    gmail.add("t1", "Q3 estimate")

    run = start(client)

    assert run["current_group"]["scope"]["mailbox"] == "INBOX"
    assert group_of(client, run["run_id"], "admin")["scope"]["name"] == "inbox"


def test_reading_a_run_back_reports_the_scope_it_was_started_with(
    client: TestClient, gmail: FakeMailbox
) -> None:
    gmail.add("t1", "Q3 estimate", label_ids=[TAXES_LABEL_ID])
    run = start(client, scope="archived")

    response = client.get(f"/review/runs/{run['run_id']}", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["scope"]["name"] == "archived"


def test_the_archive_is_reviewed_only_when_it_is_asked_for(
    client: TestClient, gmail: FakeMailbox
) -> None:
    gmail.add("inbox-thread", "Q3 estimate")
    gmail.add("archived", "Settled last year", label_ids=[TAXES_LABEL_ID])

    default = start(client)
    archived = start(client, scope="archived")

    assert threads_in(client, default["run_id"]) == {"inbox-thread"}
    assert threads_in(client, archived["run_id"]) == {"archived"}
    assert archived["scope"] == {
        **archived["scope"],
        "name": "archived",
        "mailbox": "ARCHIVE",
        "include_archived": True,
        "requested": True,
        "gmail_query": "-in:inbox -in:snoozed",
    }


def test_an_archive_review_is_its_own_run(client: TestClient, gmail: FakeMailbox) -> None:
    """Asking to see the archive must not widen the review already under way."""
    gmail.add("inbox-thread", "Q3 estimate")
    gmail.add("archived", "Settled last year", label_ids=[TAXES_LABEL_ID])

    default = start(client)
    archived = start(client, scope="archived")

    assert default["run_id"] != archived["run_id"]
    assert threads_in(client, default["run_id"]) == {"inbox-thread"}


def test_snoozed_mail_is_reviewed_only_when_it_is_asked_for(
    client: TestClient, gmail: FakeMailbox
) -> None:
    gmail.add("asleep", "Chase this in a fortnight", snoozed=True)
    gmail.add("awake", "Q3 estimate")

    review = start(client, scope="snoozed")

    assert threads_in(client, review["run_id"]) == {"asleep"}
    assert review["scope"]["gmail_query"] == "in:snoozed"
    assert review["scope"]["include_snoozed"] is True


def test_a_snoozed_review_does_not_show_archived_mail(
    client: TestClient, gmail: FakeMailbox
) -> None:
    """The bug this guards: everything out of the inbox looked snoozed.

    Snoozing publishes no label, so a check made against a thread's labels
    cannot tell a sleeping thread from one that was merely archived. Reviewing
    the archive first records that mail, and a snoozed review then had to be
    able to tell the two apart from evidence it already held.
    """
    gmail.add("archived", "Settled last year", label_ids=[TAXES_LABEL_ID])
    gmail.add("asleep", "Chase this in a fortnight", snoozed=True)

    start(client, scope="archived")
    review = start(client, scope="snoozed")

    assert threads_in(client, review["run_id"]) == {"asleep"}


def test_a_snoozed_review_of_nothing_snoozed_is_empty(
    client: TestClient, gmail: FakeMailbox
) -> None:
    """Mail nobody has ever seen asleep is not shown as asleep."""
    gmail.add("inbox-thread", "Q3 estimate")
    gmail.add("archived", "Settled last year", label_ids=[TAXES_LABEL_ID])
    start(client)
    start(client, scope="archived")

    review = start(client, scope="snoozed")

    assert threads_in(client, review["run_id"]) == set()


def test_a_thread_that_wakes_up_returns_to_the_inbox_review(
    client: TestClient, gmail: FakeMailbox
) -> None:
    """What was recorded of a snooze is corrected by the next search."""
    gmail.add("asleep", "Chase this in a fortnight", snoozed=True)
    snoozed = start(client, scope="snoozed")
    assert threads_in(client, snoozed["run_id"]) == {"asleep"}

    gmail.snoozed.discard("asleep")
    awake = start(client)

    assert threads_in(client, awake["run_id"]) == {"asleep"}


def test_reviewing_everything_says_that_is_what_it_did(
    client: TestClient, gmail: FakeMailbox
) -> None:
    gmail.add("inbox-thread", "Q3 estimate")
    gmail.add("binned", "Old receipt", label_ids=[TRASH_LABEL_ID, TAXES_LABEL_ID])

    review = start(client, scope="everything")

    assert threads_in(client, review["run_id"]) == {"inbox-thread", "binned"}
    assert review["scope"]["include_trash"] is True
    assert review["scope"]["include_spam"] is True
    assert review["scope"]["requested"] is True
    assert "in:anywhere" in review["scope"]["gmail_query"]


def test_a_scope_nobody_defined_is_refused(client: TestClient, gmail: FakeMailbox) -> None:
    response = client.post("/review/start", headers=AUTH, json={"scope": "everywhere"})

    assert response.status_code == 422
    assert "everywhere" in response.text


def test_asking_for_the_inbox_by_name_is_the_same_scope(
    client: TestClient, gmail: FakeMailbox
) -> None:
    gmail.add("t1", "Q3 estimate")

    default = start(client)
    named = start(client, scope="inbox")

    assert named["supersedes_review_id"] == default["run_id"], "one review, freshly taken"
    assert named["scope"]["mailbox"] == "INBOX"
    assert named["scope"]["requested"] is True, "the run records how it was opened"
    assert default["scope"]["requested"] is False
