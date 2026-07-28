import asyncio

from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.adapters.gmail import INBOX_LABEL_ID, GmailThread
from adminos.capabilities.config import CapabilityConfig
from adminos.db.models import Classification, Evidence
from adminos.domain.classification import classify_evidence
from adminos.domain.evidence import (
    DEFAULT_SYNC_LIMIT,
    EvidenceSyncResult,
    PruneScanTruncated,
    record_gmail_thread,
    sync_gmail_evidence,
)
from tests.conftest import build_capability


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
TAXES = build_capability()


class FakeGmailClient:
    """A GmailClient stand-in that serves canned threads per label."""

    def __init__(self, labels: dict[str, str], threads: dict[str, GmailThread]) -> None:
        self.labels = labels
        self.threads = threads
        self.fetched: list[str] = []
        self.requested_labels: list[list[str]] = []
        self.threads_by_label: dict[str, list[str]] = {}

    async def resolve_label_id(self, label_name: str) -> str | None:
        return self.labels.get(label_name)

    async def list_thread_ids(self, label_ids: Sequence[str], limit: int) -> list[str]:
        self.requested_labels.append(list(label_ids))
        wanted = self.threads_by_label.get(label_ids[-1])
        if wanted is None:
            return list(self.threads)[:limit]
        return wanted[:limit]

    async def fetch_thread(self, thread_id: str) -> GmailThread:
        self.fetched.append(thread_id)
        return self.threads[thread_id]


def build_thread(thread_id: str, subject: str, message_id: str = "m1") -> GmailThread:
    return GmailThread(
        thread_id=thread_id,
        message_id=message_id,
        subject=subject,
        participants=["cpa@example.com"],
        received_at=datetime(2026, 3, 1, tzinfo=UTC),
        snippet="Attached is the estimate.",
    )


def sync(
    client: FakeGmailClient,
    session: Session,
    capabilities: Sequence[CapabilityConfig] = (TAXES,),
    limit: int = DEFAULT_SYNC_LIMIT,
    prune: bool = False,
) -> EvidenceSyncResult:
    return asyncio.run(
        sync_gmail_evidence(client, session, capabilities, limit=limit, prune=prune)
    )


@pytest.fixture
def session(tmp_path: Path) -> Session:
    url = f"sqlite:///{tmp_path / 'evidence.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


def test_a_new_thread_is_recorded_as_evidence(session: Session) -> None:
    outcome = record_gmail_thread(session, build_thread("t1", "Q3 estimate"), ["financial_taxes"])
    session.commit()

    stored = session.query(Evidence).one()
    assert outcome == "created"
    assert stored.source_system == "gmail"
    assert stored.source_thread_id == "t1"
    assert stored.subject == "Q3 estimate"
    assert stored.participants == ["cpa@example.com"]
    assert stored.capability_keys == ["financial_taxes"]


def test_recording_the_same_thread_twice_does_not_duplicate(session: Session) -> None:
    thread = build_thread("t1", "Q3 estimate")
    record_gmail_thread(session, thread, ["financial_taxes"])
    session.commit()

    outcome = record_gmail_thread(session, thread, ["financial_taxes"])
    session.commit()

    assert outcome == "unchanged"
    assert session.query(Evidence).count() == 1


def test_a_reply_updates_the_existing_evidence_row(session: Session) -> None:
    record_gmail_thread(session, build_thread("t1", "Q3 estimate"), ["financial_taxes"])
    session.commit()

    outcome = record_gmail_thread(
        session,
        build_thread("t1", "Q3 estimate", message_id="m2"),
        ["financial_taxes"],
    )
    session.commit()

    stored = session.query(Evidence).one()
    assert outcome == "updated"
    assert stored.source_message_id == "m2"


def test_a_relabelled_thread_changes_capability(session: Session) -> None:
    """Moving a thread to another label moves it to another review group."""
    thread = build_thread("t1", "Q3 estimate")
    record_gmail_thread(session, thread, ["financial_taxes"])
    session.commit()

    outcome = record_gmail_thread(session, thread, ["admin"])
    session.commit()

    assert outcome == "updated"
    assert session.query(Evidence).one().capability_keys == ["admin"]


def test_sync_records_every_labelled_thread(session: Session) -> None:
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={
            "t1": build_thread("t1", "Q3 estimate"),
            "t2": build_thread("t2", "1099 from broker"),
        },
    )

    result = sync(client, session)
    session.commit()

    assert (result.scanned, result.created, result.updated, result.unchanged) == (2, 2, 0, 0)
    assert session.query(Evidence).count() == 2


def test_sync_attributes_each_thread_to_its_capability(session: Session) -> None:
    admin = build_capability(key="admin", labels=["Admin"], position=20)
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9", "Admin": "Label_4"},
        threads={"t1": build_thread("t1", "Q3 estimate"), "t2": build_thread("t2", "Receipt")},
    )
    client.threads_by_label = {"Label_9": ["t1"], "Label_4": ["t2"]}

    sync(client, session, [TAXES, admin])
    session.commit()

    stored = {row.source_thread_id: row.capability_keys for row in session.query(Evidence)}
    assert stored == {"t1": ["financial_taxes"], "t2": ["admin"]}


def test_a_thread_with_two_labels_belongs_to_both_capabilities(session: Session) -> None:
    admin = build_capability(key="admin", labels=["Admin"], position=20)
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9", "Admin": "Label_4"},
        threads={"t1": build_thread("t1", "Q3 estimate")},
    )
    client.threads_by_label = {"Label_9": ["t1"], "Label_4": ["t1"]}

    sync(client, session, [TAXES, admin])
    session.commit()

    assert session.query(Evidence).one().capability_keys == ["admin", "financial_taxes"]


def test_sync_scopes_intake_to_the_inbox(session: Session) -> None:
    """Archived mail is out of scope: the label alone would resurrect it."""
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={"t1": build_thread("t1", "Q3 estimate")},
    )

    sync(client, session)

    assert client.requested_labels == [[INBOX_LABEL_ID, "Label_9"]]


def test_a_capability_may_opt_out_of_the_inbox_scope(session: Session) -> None:
    capability = build_capability(gmail={"labels": ["financial/taxes"], "require_inbox": False})
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={"t1": build_thread("t1", "Q3 estimate")},
    )

    sync(client, session, [capability])

    assert client.requested_labels == [["Label_9"]]


def test_sync_is_idempotent(session: Session) -> None:
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={"t1": build_thread("t1", "Q3 estimate")},
    )

    sync(client, session)
    session.commit()
    result = sync(client, session)
    session.commit()

    assert (result.created, result.unchanged) == (0, 1)
    assert session.query(Evidence).count() == 1


def test_sync_honours_the_limit(session: Session) -> None:
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={f"t{index}": build_thread(f"t{index}", "s") for index in range(5)},
    )

    result = sync(client, session, limit=2)
    session.commit()

    assert result.scanned == 2
    assert len(client.fetched) == 2


def test_prune_removes_evidence_that_left_the_inbox(session: Session) -> None:
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={"t1": build_thread("t1", "Q3 estimate")},
    )
    session.add(
        Evidence(
            source_system="gmail",
            source_thread_id="archived",
            subject="Settled last year",
            participants=[],
            content_hash="stale",
        )
    )
    session.commit()

    result = sync(client, session, prune=True)
    session.commit()

    assert result.removed == 1
    assert [row.source_thread_id for row in session.query(Evidence)] == ["t1"]


def test_prune_takes_classifications_with_it(session: Session) -> None:
    """Without this the evidence foreign key would reject the delete outright."""
    client = FakeGmailClient(labels={"financial/taxes": "Label_9"}, threads={})
    sync(client, session)
    session.commit()
    session.add(
        Evidence(
            source_system="gmail",
            source_thread_id="archived",
            subject="Settled last year",
            participants=[],
            content_hash="stale",
        )
    )
    session.commit()
    classify_evidence(session)
    session.commit()

    result = sync(client, session, prune=True)
    session.commit()

    assert result.removed == 1
    assert session.query(Classification).count() == 0


def test_prune_spares_other_source_systems(session: Session) -> None:
    """Only Gmail evidence is in scope; a future connector's rows must survive."""
    client = FakeGmailClient(labels={"financial/taxes": "Label_9"}, threads={})
    session.add(
        Evidence(
            source_system="calendar",
            source_thread_id="event-1",
            subject="Quarterly review",
            participants=[],
            content_hash="hash",
        )
    )
    session.commit()

    sync(client, session, prune=True)
    session.commit()

    assert session.query(Evidence).one().source_system == "calendar"


def test_prune_is_refused_when_the_scan_filled_the_limit(session: Session) -> None:
    """A truncated listing cannot tell 'archived' from 'on the next page'."""
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={f"t{index}": build_thread(f"t{index}", "s") for index in range(5)},
    )

    with pytest.raises(PruneScanTruncated):
        sync(client, session, limit=3, prune=True)


def test_prune_is_refused_when_a_label_is_missing(session: Session) -> None:
    """One unresolved label makes the in-scope set incomplete, so nothing is deleted."""
    client = FakeGmailClient(labels={}, threads={})

    with pytest.raises(PruneScanTruncated):
        sync(client, session, prune=True)


def test_sync_without_prune_keeps_out_of_scope_evidence(session: Session) -> None:
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={"t1": build_thread("t1", "Q3 estimate")},
    )
    session.add(
        Evidence(
            source_system="gmail",
            source_thread_id="archived",
            subject="Settled last year",
            participants=[],
            content_hash="stale",
        )
    )
    session.commit()

    result = sync(client, session)
    session.commit()

    assert result.removed == 0
    assert session.query(Evidence).count() == 2


def test_a_missing_label_warns_and_leaves_other_capabilities_working(session: Session) -> None:
    """One mistyped label must not stop the rest of the review."""
    admin = build_capability(key="admin", labels=["Adminn"], position=20)
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={"t1": build_thread("t1", "Q3 estimate")},
    )

    result = sync(client, session, [TAXES, admin])
    session.commit()

    assert result.created == 1
    assert "Adminn" in result.warnings[0]


def test_sync_stores_no_message_body(session: Session) -> None:
    """ADR-0003: evidence keeps metadata and a snippet, never a body."""
    client = FakeGmailClient(
        labels={"financial/taxes": "Label_9"},
        threads={"t1": build_thread("t1", "Q3 estimate")},
    )

    sync(client, session)
    session.commit()

    columns = {column.name for column in Evidence.__table__.columns}
    assert "body" not in columns
    assert session.query(Evidence).one().snippet == "Attached is the estimate."
