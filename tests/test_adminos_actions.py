import asyncio

from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.adapters.gmail import GmailDraft, GmailError, GmailThread
from adminos.capabilities.config import ActionKind, CapabilityConfig, LoadedCapabilities
from adminos.db.models import Evidence, ReviewAction, ReviewItem, ReviewRun
from adminos.domain.actions import (
    ActionEventKind,
    ActionRefused,
    ActionState,
    ApprovalKind,
    EXECUTORS,
    authorise_send,
    ensure_actions,
    execute_action,
    idempotency_key,
    prepare_action,
    read_action_events,
    verify_action,
)
from adminos.domain.decisions import HUMAN_ACTOR, DecisionKind, ItemState
from adminos.domain.review import record_decision, start_or_resume_review
from tests.conftest import build_capability


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
THREAD = "t1"

ADMIN = build_capability(key="admin", labels=["Admin"], position=10)
NO_EXECUTION = build_capability(
    key="admin",
    labels=["Admin"],
    execution={"permitted_actions": []},
)


class FakeGmail:
    """A mailbox that records writes, so a test can see what actually happened."""

    def __init__(
        self,
        labels: dict[str, str] | None = None,
        thread_labels: Sequence[str] = ("INBOX", "Label_admin"),
    ) -> None:
        self.labels = labels or {"Admin": "Label_admin", "Reviewed": "Label_reviewed"}
        self.thread_labels = list(thread_labels)
        self.drafts: dict[str, GmailDraft] = {}
        self.sent: list[str] = []
        self.writes: list[tuple[str, object]] = []
        self.fail_next_write: Exception | None = None
        self.silently_ignore_writes = False
        self.next_draft_id = 1

    async def resolve_label_id(self, label_name: str) -> str | None:
        return self.labels.get(label_name)

    async def fetch_thread(self, thread_id: str) -> GmailThread:
        return GmailThread(
            thread_id=thread_id,
            message_id="m1",
            subject="Renew the parking permit",
            participants=["clerk@council.gov"],
            received_at=NOW,
            snippet=None,
            label_ids=list(self.thread_labels),
        )

    async def modify_thread(
        self,
        thread_id: str,
        add_label_ids: Sequence[str] = (),
        remove_label_ids: Sequence[str] = (),
    ) -> GmailThread:
        self.raise_if_asked()
        self.writes.append(("modify", (thread_id, list(add_label_ids), list(remove_label_ids))))
        if not self.silently_ignore_writes:
            for label_id in add_label_ids:
                if label_id not in self.thread_labels:
                    self.thread_labels.append(label_id)
            for label_id in remove_label_ids:
                if label_id in self.thread_labels:
                    self.thread_labels.remove(label_id)
        return await self.fetch_thread(thread_id)

    async def create_draft(
        self,
        thread_id: str,
        to: Sequence[str],
        subject: str,
        body: str,
        cc: Sequence[str] = (),
    ) -> GmailDraft:
        self.raise_if_asked()
        draft_id = f"draft-{self.next_draft_id}"
        self.next_draft_id += 1
        draft = GmailDraft(draft_id=draft_id, message_id=f"msg-{draft_id}", thread_id=thread_id)
        self.drafts[draft_id] = draft
        self.writes.append(("draft", (thread_id, subject, body)))
        return draft

    async def fetch_draft(self, draft_id: str) -> GmailDraft | None:
        return self.drafts.get(draft_id)

    async def find_draft_for_thread(self, thread_id: str) -> GmailDraft | None:
        for draft in self.drafts.values():
            if draft.thread_id == thread_id:
                return draft
        return None

    async def send_draft(self, draft_id: str) -> str | None:
        self.raise_if_asked()
        draft = self.drafts.pop(draft_id)
        self.sent.append(draft_id)
        self.writes.append(("send", draft_id))
        return draft.message_id

    def raise_if_asked(self) -> None:
        if self.fail_next_write is not None:
            error, self.fail_next_write = self.fail_next_write, None
            raise error


@pytest.fixture
def session(tmp_path: Path) -> Session:
    url = f"sqlite:///{tmp_path / 'actions.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


@pytest.fixture(autouse=True)
def gmail_writes_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_WRITE_ENABLED", "true")


def approve(
    session: Session,
    capability: CapabilityConfig = ADMIN,
    action: ActionKind = ActionKind.GMAIL_ARCHIVE,
    params: dict[str, object] | None = None,
) -> tuple[ReviewAction, ReviewItem, ReviewRun]:
    """Take one thread all the way to an approved, recorded action."""
    session.add(
        Evidence(
            source_system="gmail",
            source_thread_id=THREAD,
            subject="Renew the parking permit",
            participants=["clerk@council.gov"],
            received_at=NOW,
            content_hash="hash-t1",
            capability_keys=[capability.key],
        )
    )
    session.flush()

    view = start_or_resume_review(
        session,
        LoadedCapabilities(
            version="test.1", digest="d" * 64, channel="email", capabilities=(capability,)
        ),
        now=NOW,
    )
    item = view.groups[0].items[0]
    record_decision(
        session,
        capability,
        view.run,
        item,
        DecisionKind.OVERRIDE,
        action=action,
        action_params=params,
        now=NOW,
    )
    actions = ensure_actions(session, capability, view.run, [item], now=NOW)
    return actions[0], item, view.run


def run_execute(
    session: Session,
    client: FakeGmail,
    action: ReviewAction,
    capability: CapabilityConfig = ADMIN,
) -> ReviewAction:
    prepare_action(session, capability, action, now=NOW)
    return asyncio.run(execute_action(session, client, capability, action, now=NOW))


def test_approving_records_an_action_but_writes_nothing(session: Session) -> None:
    action, item, _ = approve(session)

    assert action.state == ActionState.APPROVED
    assert item.state == ItemState.APPROVED
    assert action.executed_at is None
    assert [event.event for event in read_action_events(session, action)] == [
        ActionEventKind.APPROVED
    ]


def test_the_same_approval_records_one_action(session: Session) -> None:
    _, item, run = approve(session)

    again = ensure_actions(session, ADMIN, run, [item], now=NOW)

    assert len(again) == 1
    assert session.query(ReviewAction).count() == 1


def test_the_idempotency_key_is_stable_for_the_same_intent() -> None:
    first = idempotency_key("item-1", ActionKind.GMAIL_LABEL, {"add_labels": ["Admin"]})
    second = idempotency_key("item-1", ActionKind.GMAIL_LABEL, {"add_labels": ["Admin"]})
    third = idempotency_key("item-1", ActionKind.GMAIL_LABEL, {"add_labels": ["Other"]})

    assert first == second
    assert first != third


def test_preparing_resolves_the_exact_parameters_without_touching_gmail(
    session: Session,
) -> None:
    action, _, _ = approve(
        session, action=ActionKind.GMAIL_LABEL, params={"add_labels": ["Reviewed"]}
    )

    prepared = prepare_action(session, ADMIN, action, now=NOW)

    assert prepared.state == ActionState.PREPARED
    assert prepared.prepared_params == {"add_labels": ["Reviewed"], "remove_labels": []}


def test_preparing_twice_changes_nothing(session: Session) -> None:
    action, _, _ = approve(session)

    prepare_action(session, ADMIN, action, now=NOW)
    first = action.prepared_at
    prepare_action(session, ADMIN, action, now=NOW)

    assert action.prepared_at == first
    assert len(read_action_events(session, action)) == 2


def test_a_capability_that_may_not_execute_is_refused(session: Session) -> None:
    action, _, _ = approve(session, capability=NO_EXECUTION)

    with pytest.raises(ActionRefused) as error:
        prepare_action(session, NO_EXECUTION, action, now=NOW)

    assert "not permitted to execute" in str(error.value)


def test_the_kill_switch_stops_execution(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    action, _, _ = approve(session)
    prepare_action(session, ADMIN, action, now=NOW)
    monkeypatch.setenv("GMAIL_WRITE_ENABLED", "false")
    client = FakeGmail()

    with pytest.raises(ActionRefused) as error:
        asyncio.run(execute_action(session, client, ADMIN, action, now=NOW))

    assert "Gmail writes are disabled" in str(error.value)
    assert client.writes == []


def test_permission_is_rechecked_when_the_action_runs(session: Session) -> None:
    action, _, _ = approve(session)
    prepare_action(session, ADMIN, action, now=NOW)
    withdrawn = build_capability(
        key="admin", labels=["Admin"], execution={"permitted_actions": ["gmail.label"]}
    )

    with pytest.raises(ActionRefused):
        asyncio.run(execute_action(session, FakeGmail(), withdrawn, action, now=NOW))


def test_an_unprepared_action_will_not_execute(session: Session) -> None:
    action, _, _ = approve(session)

    with pytest.raises(ActionRefused) as error:
        asyncio.run(execute_action(session, FakeGmail(), ADMIN, action, now=NOW))

    assert "must be prepared" in str(error.value)


def test_archiving_removes_the_inbox_label_and_is_verified(session: Session) -> None:
    action, item, _ = approve(session)
    client = FakeGmail()

    executed = run_execute(session, client, action)

    assert client.writes == [("modify", (THREAD, [], ["INBOX"]))]
    assert executed.state == ActionState.COMPLETED
    assert executed.verification == {"labels": ["Label_admin"]}
    assert item.state == ItemState.EXECUTED


def test_labelling_adds_and_removes_by_resolved_id(session: Session) -> None:
    action, _, _ = approve(
        session,
        action=ActionKind.GMAIL_LABEL,
        params={"add_labels": ["Reviewed"], "remove_labels": ["Admin"]},
    )
    client = FakeGmail()

    executed = run_execute(session, client, action)

    assert client.writes == [("modify", (THREAD, ["Label_reviewed"], ["Label_admin"]))]
    assert executed.state == ActionState.COMPLETED


def test_a_label_the_mailbox_does_not_have_fails_rather_than_being_created(
    session: Session,
) -> None:
    action, _, _ = approve(
        session, action=ActionKind.GMAIL_LABEL, params={"add_labels": ["Invented"]}
    )
    client = FakeGmail()

    executed = run_execute(session, client, action)

    assert executed.state == ActionState.FAILED
    assert "no label named 'Invented'" in (executed.last_error or "")
    assert client.writes == []


def test_the_inbox_label_cannot_be_set_by_hand(session: Session) -> None:
    action, _, _ = approve(
        session, action=ActionKind.GMAIL_LABEL, params={"remove_labels": ["INBOX"]}
    )

    with pytest.raises(ActionRefused) as error:
        prepare_action(session, ADMIN, action, now=NOW)

    assert "archive is its own action" in str(error.value)


def test_a_draft_is_created_and_read_back_without_being_sent(session: Session) -> None:
    action, _, _ = approve(
        session,
        action=ActionKind.GMAIL_DRAFT_REPLY,
        params={"to": ["clerk@council.gov"], "body": "Permit renewed, thank you."},
    )
    client = FakeGmail()

    executed = run_execute(session, client, action)

    assert executed.state == ActionState.COMPLETED
    assert executed.external_ref == "draft-1"
    assert executed.verification == {
        "draft_id": "draft-1",
        "message_id": "msg-draft-1",
        "sent": False,
    }
    assert client.sent == []


def test_a_draft_without_a_body_is_refused(session: Session) -> None:
    action, _, _ = approve(
        session,
        action=ActionKind.GMAIL_DRAFT_REPLY,
        params={"to": ["clerk@council.gov"]},
    )

    with pytest.raises(ActionRefused) as error:
        prepare_action(session, ADMIN, action, now=NOW)

    assert "nothing is written on your behalf" in str(error.value)


def test_a_failed_write_is_durable_and_retryable(session: Session) -> None:
    action, item, _ = approve(session)
    client = FakeGmail()
    client.fail_next_write = GmailError("Gmail is unavailable.")

    failed = run_execute(session, client, action)

    assert failed.state == ActionState.FAILED
    assert failed.attempts == 1
    assert item.state == ItemState.FAILED
    assert [event.event for event in read_action_events(session, action)][-1] == (
        ActionEventKind.FAILED
    )

    retried = asyncio.run(execute_action(session, client, ADMIN, action, now=NOW))

    assert retried.state == ActionState.COMPLETED
    assert retried.attempts == 2
    assert item.state == ItemState.EXECUTED


def test_a_retry_adopts_an_effect_that_already_landed(session: Session) -> None:
    """The dangerous case: Gmail did the work, then the response was lost."""
    action, _, _ = approve(
        session,
        action=ActionKind.GMAIL_DRAFT_REPLY,
        params={"to": ["clerk@council.gov"], "body": "Thanks."},
    )
    client = FakeGmail()
    client.drafts["draft-99"] = GmailDraft(
        draft_id="draft-99", message_id="msg-99", thread_id=THREAD
    )
    client.fail_next_write = GmailError("Gmail timed out.")
    run_execute(session, client, action)

    retried = asyncio.run(execute_action(session, client, ADMIN, action, now=NOW))

    assert retried.state == ActionState.COMPLETED
    assert retried.external_ref == "draft-99"
    assert len(client.drafts) == 1
    assert ActionEventKind.ADOPTED_EXISTING in {
        event.event for event in read_action_events(session, action)
    }


def test_a_completed_action_does_not_run_again(session: Session) -> None:
    action, _, _ = approve(session)
    client = FakeGmail()
    run_execute(session, client, action)

    asyncio.run(execute_action(session, client, ADMIN, action, now=NOW))

    assert len(client.writes) == 1


def test_a_change_gmail_does_not_show_is_a_visible_failure(session: Session) -> None:
    action, item, _ = approve(session)
    client = FakeGmail()
    client.silently_ignore_writes = True

    executed = run_execute(session, client, action)

    assert executed.state == ActionState.FAILED
    assert executed.last_error == "Gmail did not show the change after it was made."
    assert item.state == ItemState.FAILED
    assert ActionEventKind.VERIFICATION_FAILED in {
        event.event for event in read_action_events(session, action)
    }


def test_verifying_again_re_reads_gmail_without_writing(session: Session) -> None:
    action, _, _ = approve(session)
    client = FakeGmail()
    run_execute(session, client, action)

    verified = asyncio.run(verify_action(session, client, ADMIN, action, now=NOW))

    assert verified.state == ActionState.COMPLETED
    assert len(client.writes) == 1


def test_there_is_no_executor_for_permanent_deletion() -> None:
    """Deletion is not gated, it is absent: nothing can perform it."""
    assert set(EXECUTORS) == {
        ActionKind.GMAIL_LABEL,
        ActionKind.GMAIL_ARCHIVE,
        ActionKind.GMAIL_DRAFT_REPLY,
        ActionKind.GMAIL_SEND_DRAFT,
    }
    assert ActionKind.GMAIL_TRASH not in EXECUTORS


def test_sending_requires_the_exact_draft_that_was_reviewed(session: Session) -> None:
    sender = build_capability(
        key="admin",
        labels=["Admin"],
        allowed_actions=[
            "gmail.label",
            "gmail.archive",
            "gmail.draft_reply",
            "gmail.send_draft",
        ],
        execution={
            "permitted_actions": ["gmail.archive", "gmail.draft_reply", "gmail.send_draft"]
        },
    )
    draft, item, run = approve(
        session,
        capability=sender,
        action=ActionKind.GMAIL_DRAFT_REPLY,
        params={"to": ["clerk@council.gov"], "body": "Thanks."},
    )
    client = FakeGmail()
    run_execute(session, client, draft, capability=sender)

    with pytest.raises(ActionRefused) as error:
        authorise_send(
            session,
            sender,
            run,
            item,
            draft_id="draft-1",
            draft_message_id="msg-somebody-elses",
            actor=HUMAN_ACTOR,
        )

    assert "not the draft that was reviewed" in str(error.value)
    assert client.sent == []


def test_an_approved_send_still_has_to_be_prepared_and_executed(session: Session) -> None:
    sender = build_capability(
        key="admin",
        labels=["Admin"],
        allowed_actions=[
            "gmail.label",
            "gmail.archive",
            "gmail.draft_reply",
            "gmail.send_draft",
        ],
        execution={
            "permitted_actions": ["gmail.archive", "gmail.draft_reply", "gmail.send_draft"]
        },
    )
    draft, item, run = approve(
        session,
        capability=sender,
        action=ActionKind.GMAIL_DRAFT_REPLY,
        params={"to": ["clerk@council.gov"], "body": "Thanks."},
    )
    client = FakeGmail()
    run_execute(session, client, draft, capability=sender)

    send = authorise_send(
        session,
        sender,
        run,
        item,
        draft_id="draft-1",
        draft_message_id="msg-draft-1",
        actor=HUMAN_ACTOR,
    )

    assert send.state == ActionState.APPROVED
    assert send.approval_kind == ApprovalKind.HUMAN
    assert client.sent == []

    sent = run_execute(session, client, send, capability=sender)

    assert sent.state == ActionState.COMPLETED
    assert client.sent == ["draft-1"]
    assert sent.verification == {"draft_id": "draft-1", "draft_still_present": False}


def test_a_draft_edited_after_approval_will_not_send(session: Session) -> None:
    sender = build_capability(
        key="admin",
        labels=["Admin"],
        allowed_actions=[
            "gmail.label",
            "gmail.archive",
            "gmail.draft_reply",
            "gmail.send_draft",
        ],
        execution={
            "permitted_actions": ["gmail.archive", "gmail.draft_reply", "gmail.send_draft"]
        },
    )
    draft, item, run = approve(
        session,
        capability=sender,
        action=ActionKind.GMAIL_DRAFT_REPLY,
        params={"to": ["clerk@council.gov"], "body": "Thanks."},
    )
    client = FakeGmail()
    run_execute(session, client, draft, capability=sender)
    send = authorise_send(
        session,
        sender,
        run,
        item,
        draft_id="draft-1",
        draft_message_id="msg-draft-1",
        actor=HUMAN_ACTOR,
    )
    client.drafts["draft-1"] = GmailDraft(
        draft_id="draft-1", message_id="msg-rewritten", thread_id=THREAD
    )

    executed = run_execute(session, client, send, capability=sender)

    assert executed.state == ActionState.FAILED
    assert "changed since it was approved" in (executed.last_error or "")
    assert client.sent == []
