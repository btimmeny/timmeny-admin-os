"""A session runs one revision of the playbook against the state of the day.

What these hold to: the plan comes from configuration rather than from whoever
is talking, a session keeps the revision it opened with even after the playbook
changes, an activity nobody has built is said to be that rather than skipped
quietly, and finishing an activity is never the same claim as the mailbox
having been changed.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.capabilities.config import CapabilityConfig, LoadedCapabilities
from adminos.db.models import Evidence, ReviewRun
from adminos.domain.playbook import read_change
from adminos.domain.playbook_store import (
    ActivePlaybook,
    confirm_revision,
    propose_change,
    read_active_playbook,
)
from adminos.domain.review import DecisionKind, RunState, record_decision, refresh_states
from adminos.domain.sessions import (
    ActivityState,
    SessionNotFound,
    SessionRefused,
    SessionStatus,
    advance_session,
    begin_session,
    continue_session,
    open_session,
    read_session_view,
    session_playbook,
)
from tests.conftest import build_capability


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
TEST_PLAYBOOK = REPOSITORY_ROOT / "tests/data/playbook_pair.yaml"
NOW = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)

ADMIN = build_capability(key="admin", labels=("Admin",), position=10)
TAXES = build_capability(key="financial_taxes", position=20)


def load() -> LoadedCapabilities:
    capabilities: tuple[CapabilityConfig, ...] = (ADMIN, TAXES)
    return LoadedCapabilities(
        version="test.1",
        digest="d" * 64,
        channel="email",
        capabilities=capabilities,
    )


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    url = f"sqlite:///{tmp_path / 'sessions.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    monkeypatch.setenv("ASSISTANT_PLAYBOOK_PATH", str(TEST_PLAYBOOK))

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


def playbook(session: Session) -> ActivePlaybook:
    return read_active_playbook(session, load(), now=NOW)


def add_evidence(
    session: Session, thread_id: str, subject: str, capability: str = "admin"
) -> Evidence:
    evidence = Evidence(
        source_system="gmail",
        source_thread_id=thread_id,
        subject=subject,
        participants=["someone@example.com"],
        received_at=NOW,
        content_hash=f"hash-{thread_id}",
        capability_keys=[capability],
        label_ids=["INBOX"],
    )
    session.add(evidence)
    session.flush()
    return evidence


def mailbox(session: Session) -> None:
    add_evidence(session, "t1", "Domain renewal", "admin")
    add_evidence(session, "t2", "KPMG Activities", "financial_taxes")


def test_a_session_states_the_playbook_order_and_waits(session: Session) -> None:
    mailbox(session)

    view = open_session(session, load(), playbook(session), now=NOW)

    assert view.row.status == SessionStatus.PROPOSED
    assert view.row.begun_at is None
    assert [activity.row.activity_key for activity in view.activities] == [
        "email_review",
        "objectives_review",
        "session_closeout",
    ]
    assert view.current() is None


def test_the_first_activity_reports_the_steps_the_playbook_configured(
    session: Session,
) -> None:
    """The order inside an activity is configuration, not the review's own idea."""
    mailbox(session)

    view = open_session(session, load(), playbook(session), now=NOW)
    email = view.find("email_review")

    assert email is not None
    assert [step.key for step in email.steps] == ["admin", "financial_taxes"]
    assert [step.label for step in email.steps] == ["Admin", "Financial/Taxes"]
    assert [step.count for step in email.steps] == [1, 1]


def test_an_activity_that_is_not_built_is_reported_rather_than_dropped(
    session: Session,
) -> None:
    """Objectives is in the playbook. Pretending it is not would be the lie."""
    mailbox(session)

    view = open_session(session, load(), playbook(session), now=NOW)
    objectives = view.find("objectives_review")

    assert objectives is not None
    assert objectives.row.state == ActivityState.UNAVAILABLE
    assert "objectives_review" not in [
        activity.row.activity_key for activity in view.workable()
    ]


def test_beginning_a_session_works_the_first_activity_that_can_be_worked(
    session: Session,
) -> None:
    mailbox(session)
    view = open_session(session, load(), playbook(session), now=NOW)

    begun = begin_session(session, load(), view.row, playbook(session), now=NOW)

    assert begun.row.status == SessionStatus.IN_PROGRESS
    assert begun.row.current_activity_key == "email_review"
    assert begun.review is not None
    assert begun.review.run.state == RunState.NOT_STARTED
    assert begun.review.current_group() is not None


def test_the_email_review_is_worked_in_the_playbook_step_order(session: Session) -> None:
    """Admin before Financial/Taxes because the playbook says so, not the config."""
    mailbox(session)
    view = open_session(session, load(), playbook(session), now=NOW)

    begun = begin_session(session, load(), view.row, playbook(session), now=NOW)
    group = begun.review.current_group() if begun.review else None

    assert group is not None
    assert group.group.capability_key == "admin"


def test_a_session_only_reordering_does_not_touch_the_playbook(session: Session) -> None:
    """The line the design turns on: today's order is not tomorrow's playbook."""
    mailbox(session)

    view = open_session(
        session,
        load(),
        playbook(session),
        order=["session_closeout", "email_review"],
        now=NOW,
    )
    after = playbook(session)

    assert [activity.row.activity_key for activity in view.activities][:2] == [
        "session_closeout",
        "email_review",
    ]
    assert [activity.activity_key for activity in after.document.enabled()][0] == "email_review"
    assert view.row.overrides is not None
    assert "not the playbook's order" in view.row.overrides[0]


def test_skipping_an_activity_for_today_is_recorded_on_the_session(
    session: Session,
) -> None:
    mailbox(session)

    view = open_session(session, load(), playbook(session), skip=["email_review"], now=NOW)
    email = view.find("email_review")

    assert email is not None
    assert email.row.state == ActivityState.SKIPPED
    assert email.row.run_id is None
    assert view.row.skipped == ["email_review"]
    assert view.row.overrides == ["Email is set aside for this session only."]
    assert [activity.activity_key for activity in playbook(session).document.enabled()] == [
        "email_review",
        "objectives_review",
        "session_closeout",
    ]


def test_naming_an_activity_the_playbook_does_not_have_is_refused(
    session: Session,
) -> None:
    with pytest.raises(SessionRefused) as refusal:
        open_session(session, load(), playbook(session), skip=["legal_review"], now=NOW)

    assert "legal_review" in str(refusal.value)


def test_a_session_keeps_the_revision_it_opened_with(session: Session) -> None:
    """A change agreed mid-morning does not rearrange the morning."""
    mailbox(session)
    opened = open_session(session, load(), playbook(session), now=NOW)
    first = opened.row.playbook_revision_id

    proposal = propose_change(
        session,
        load(),
        [read_change({"operation": "disable_activity", "activity_key": "objectives_review"})],
        now=NOW,
    )
    confirm_revision(session, load(), proposal.revision.id, now=NOW)
    resumed = continue_session(session, load(), playbook(session))

    assert resumed.playbook.revision.id == first
    assert "objectives_review" in [
        activity.row.activity_key for activity in resumed.activities
    ]
    assert playbook(session).revision.id != first


def test_saying_hello_again_sets_the_open_session_aside(session: Session) -> None:
    mailbox(session)
    first = open_session(session, load(), playbook(session), now=NOW)

    second = open_session(
        session, load(), playbook(session), now=NOW + timedelta(hours=1)
    )

    assert second.row.id != first.row.id
    assert second.row.supersedes_session_id == first.row.id
    assert first.row.status == SessionStatus.ABANDONED
    assert second.review is not None and first.review is not None
    assert second.review.run.id != first.review.run.id


def test_continuing_when_nothing_is_under_way_says_so(session: Session) -> None:
    with pytest.raises(SessionNotFound):
        continue_session(session, load(), playbook(session))


def test_an_activity_is_not_finished_while_its_review_is_unworked(
    session: Session,
) -> None:
    """Advancing must ask the work, not the session's own optimism."""
    mailbox(session)
    view = open_session(session, load(), playbook(session), now=NOW)
    begun = begin_session(session, load(), view.row, playbook(session), now=NOW)

    advanced = advance_session(session, load(), begun.row, playbook(session), now=NOW)
    email = advanced.find("email_review")

    assert email is not None
    assert email.row.state == ActivityState.IN_PROGRESS
    assert advanced.row.status == SessionStatus.IN_PROGRESS


def test_a_worked_review_lets_the_session_move_on_to_the_closeout(
    session: Session,
) -> None:
    mailbox(session)
    view = open_session(session, load(), playbook(session), now=NOW)
    begun = begin_session(session, load(), view.row, playbook(session), now=NOW)
    work(session, begun.row.id)

    advanced = advance_session(session, load(), begun.row, playbook(session), now=NOW)
    email = advanced.find("email_review")

    assert email is not None
    assert email.row.state == ActivityState.COMPLETED
    assert advanced.row.current_activity_key == "session_closeout"
    assert advanced.row.status == SessionStatus.IN_PROGRESS


def test_the_closeout_finishes_the_session_and_counts_verified_work(
    session: Session,
) -> None:
    mailbox(session)
    view = open_session(session, load(), playbook(session), now=NOW)
    begun = begin_session(session, load(), view.row, playbook(session), now=NOW)
    work(session, begun.row.id)
    announced = advance_session(session, load(), begun.row, playbook(session), now=NOW)
    closeout = announced.find("session_closeout")
    assert closeout is not None and closeout.row.state == ActivityState.PENDING

    begin_session(session, load(), begun.row, playbook(session), now=NOW)
    finished = advance_session(session, load(), begun.row, playbook(session), now=NOW)

    assert finished.row.status == SessionStatus.COMPLETED
    assert finished.summary is not None
    assert finished.summary.reviewed == 2
    assert finished.summary.done == {}
    assert finished.summary.dismissed == 2


def test_reading_a_session_back_reports_it_without_changing_it(session: Session) -> None:
    mailbox(session)
    view = open_session(session, load(), playbook(session), now=NOW)

    read = read_session_view(
        session, load(), view.row, session_playbook(session, playbook(session), view.row)
    )

    assert read.row.id == view.row.id
    assert read.row.status == SessionStatus.PROPOSED
    assert [activity.row.state for activity in read.activities] == [
        ActivityState.PENDING,
        ActivityState.UNAVAILABLE,
        ActivityState.PENDING,
    ]


def work(session: Session, session_id: str) -> None:
    """Settle every row of the session's review, the way a morning does."""
    row = session.get(ReviewRun, review_id(session, session_id))
    assert row is not None
    view = refresh_states(session, load(), row, now=NOW)
    for group in view.groups:
        capability = ADMIN if group.group.capability_key == "admin" else TAXES
        for item in group.items:
            record_decision(session, capability, row, item, DecisionKind.DISMISS, now=NOW)
    refresh_states(session, load(), row, now=NOW)


def review_id(session: Session, session_id: str) -> str:
    from adminos.db.models import SessionActivity

    activity = (
        session.query(SessionActivity)
        .filter(
            SessionActivity.session_id == session_id,
            SessionActivity.activity_key == "email_review",
        )
        .one()
    )
    assert activity.run_id is not None
    return activity.run_id
