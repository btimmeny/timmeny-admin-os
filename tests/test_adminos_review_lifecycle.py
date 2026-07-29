"""A review is a thing with a beginning and an end, not a query re-run.

"Start my review" on a day already reviewed used to hand back the morning's
work with whatever had arrived since bolted on, which reads as unfinished. The
lifecycle here is the answer: a review is created, worked, and finished; a
finished one is left alone; and starting the day again is a separate sentence
that abandons the first and opens a second revision on refreshed mail.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Sequence

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.capabilities.config import CapabilityConfig, LoadedCapabilities
from adminos.db.models import ActionScope, Evidence, ReviewItem, ReviewRun
from adminos.domain.mailboxes import read_scope
from adminos.domain.review import (
    DecisionKind,
    ItemState,
    ReviewClosed,
    ReviewNotFound,
    RunState,
    RunView,
    continue_review,
    record_decision,
    refresh_states,
    restart_review,
    start_or_resume_review,
)
from adminos.domain.scopes import ScopeState, open_scope
from tests.conftest import build_capability


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
TODAY = NOW.date()
TOMORROW = date(2026, 7, 29)

TAXES = build_capability(key="financial_taxes")


def load(*capabilities: CapabilityConfig) -> LoadedCapabilities:
    return LoadedCapabilities(
        version="test.1",
        digest="d" * 64,
        channel="email",
        capabilities=capabilities or (TAXES,),
    )


@pytest.fixture
def session(tmp_path: Path) -> Session:
    url = f"sqlite:///{tmp_path / 'lifecycle.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


def add_evidence(
    session: Session,
    thread_id: str,
    subject: str = "KPMG Activities",
    capabilities: Sequence[str] = ("financial_taxes",),
    received_at: datetime | None = None,
) -> Evidence:
    evidence = Evidence(
        source_system="gmail",
        source_thread_id=thread_id,
        subject=subject,
        participants=["cpa@kpmg.com"],
        received_at=received_at or NOW,
        content_hash=f"hash-{thread_id}",
        capability_keys=list(capabilities),
        label_ids=["INBOX"],
    )
    session.add(evidence)
    session.flush()
    return evidence


def start(session: Session, now: datetime = NOW, **kwargs: object) -> RunView:
    return start_or_resume_review(session, load(), now=now, **kwargs)  # type: ignore[arg-type]


def dismiss_everything(session: Session, view: RunView, now: datetime = NOW) -> None:
    for group in view.groups:
        for item in group.items:
            record_decision(session, TAXES, view.run, item, DecisionKind.DISMISS, now=now)
    refresh_states(session, load(), view.run, now=now)


def test_the_first_review_of_the_day_is_created_and_not_yet_started(session: Session) -> None:
    add_evidence(session, "t1")

    view = start(session, evidence_refresh_at=NOW)

    assert view.run.revision == 1
    assert view.run.state == RunState.NOT_STARTED
    assert view.run.started_at == NOW
    assert view.run.evidence_refresh_at == NOW
    assert view.run.completed_at is None
    assert view.run.abandoned_at is None


def test_a_decision_moves_the_review_from_not_started_to_in_progress(
    session: Session,
) -> None:
    add_evidence(session, "t1")
    add_evidence(session, "t2", "IRS notice")
    view = start(session)

    record_decision(session, TAXES, view.run, view.groups[0].items[0], DecisionKind.DISMISS)
    resumed = continue_review(session, load(), now=NOW + timedelta(minutes=5))

    assert resumed.run.id == view.run.id
    assert resumed.run.state == RunState.IN_PROGRESS


def test_an_interrupted_review_is_resumed_with_its_decisions_intact(session: Session) -> None:
    """Resuming is the ordinary case, and it must not cost a decision."""
    add_evidence(session, "t1")
    add_evidence(session, "t2", "IRS notice")
    first = start(session)
    decided = record_decision(
        session, TAXES, first.run, first.groups[0].items[0], DecisionKind.DISMISS
    )
    session.commit()

    resumed = continue_review(session, load(), now=NOW + timedelta(hours=2))

    assert resumed.run.id == first.run.id
    assert resumed.run.revision == 1
    states = {item.id: item.state for item in resumed.groups[0].items}
    assert states[decided.id] == ItemState.DISMISSED
    assert len(states) == 2


def test_continuing_a_review_that_was_never_started_says_so(session: Session) -> None:
    add_evidence(session, "t1")

    with pytest.raises(ReviewNotFound):
        continue_review(session, load(), now=NOW)


def test_starting_a_completed_review_refuses_rather_than_reopening_it(
    session: Session,
) -> None:
    """The bug this exists for: mail arriving cannot un-finish a morning."""
    add_evidence(session, "t1")
    view = start(session)
    dismiss_everything(session, view)
    session.commit()
    add_evidence(session, "t2", "IRS notice")

    with pytest.raises(ReviewClosed) as refused:
        start(session, now=NOW + timedelta(hours=3))

    assert refused.value.run.id == view.run.id
    assert refused.value.run.state == RunState.COMPLETED
    assert session.query(ReviewItem).count() == 1


def test_an_empty_review_is_topped_up_rather_than_treated_as_a_day_of_work(
    session: Session,
) -> None:
    """Nothing in the inbox at eight is not a morning to protect at ten."""
    first = start(session)
    assert first.run.state == RunState.COMPLETED

    add_evidence(session, "t1")
    resumed = start(session, now=NOW + timedelta(hours=2))

    assert resumed.run.id == first.run.id
    assert [item.source_thread_id for item in resumed.groups[0].items] == ["t1"]


def test_restarting_abandons_the_old_review_and_opens_the_next_revision(
    session: Session,
) -> None:
    """The new review holds t2 alone: t1 was answered, and stays answered."""
    add_evidence(session, "t1")
    first = start(session)
    dismiss_everything(session, first)
    session.commit()
    add_evidence(session, "t2", "IRS notice")

    later = NOW + timedelta(hours=3)
    second = restart_review(session, load(), now=later, evidence_refresh_at=later)

    assert second.run.id != first.run.id
    assert second.run.revision == 2
    assert second.run.review_date == first.run.review_date
    assert second.run.evidence_refresh_at == later
    assert first.run.state == RunState.ABANDONED
    assert first.run.abandoned_at == later
    assert [item.source_thread_id for item in second.groups[0].items] == ["t2"]


def test_the_abandoned_review_keeps_what_was_decided_in_it(session: Session) -> None:
    """Abandoning ends a session. It does not rewrite what happened in it."""
    add_evidence(session, "t1")
    first = start(session)
    decided = record_decision(
        session, TAXES, first.run, first.groups[0].items[0], DecisionKind.DISMISS
    )
    restart_review(session, load(), now=NOW + timedelta(hours=1))

    kept = session.get(ReviewItem, decided.id)

    assert kept is not None
    assert kept.state == ItemState.DISMISSED
    assert kept.run_id == first.run.id


def test_an_abandoned_review_is_never_resumed_again(session: Session) -> None:
    add_evidence(session, "t1")
    first = start(session)
    restart_review(session, load(), now=NOW + timedelta(hours=1))
    session.commit()

    resumed = continue_review(session, load(), now=NOW + timedelta(hours=2))

    assert resumed.run.id != first.run.id
    assert resumed.run.revision == 2
    assert session.get(ReviewRun, first.run.id).state == RunState.ABANDONED


def test_restarting_disarms_a_scope_prepared_in_the_old_review(session: Session) -> None:
    """A confirmation given before the restart cannot run after it."""
    add_evidence(session, "t1")
    first = start(session)
    scope = open_scope(
        session,
        first.run,
        capability_key="financial_taxes",
        entire_capability=False,
        requested_item_ids=[first.groups[0].items[0].id],
        actions=[],
        excluded=[],
        actor="human",
        now=NOW,
    )

    restart_review(session, load(), now=NOW + timedelta(hours=1))

    assert session.get(ActionScope, scope.id).state == ScopeState.SUPERSEDED


def test_a_new_review_carries_none_of_the_old_review_s_rows(session: Session) -> None:
    """Counts belong to one review: a restart starts the progress over.

    The thread still outstanding is presented again, as a row of the new
    review rather than the old one, so nothing a caller does to it can reach
    across into a review that was set aside.
    """
    add_evidence(session, "t1")
    add_evidence(session, "t2", "IRS notice")
    first = start(session)
    answered = next(item for item in first.groups[0].items if item.source_thread_id == "t1")
    record_decision(session, TAXES, first.run, answered, DecisionKind.DISMISS)
    second = restart_review(session, load(), now=NOW + timedelta(hours=1))

    rows = second.groups[0].items

    assert [item.source_thread_id for item in rows] == ["t2"]
    assert all(item.run_id == second.run.id for item in rows)
    assert all(item.state == ItemState.PENDING for item in rows)
    assert {item.id for item in rows}.isdisjoint({item.id for item in first.groups[0].items})


def test_the_calendar_day_turning_over_opens_a_new_review_by_itself(
    session: Session,
) -> None:
    add_evidence(session, "t1")
    today = start(session)
    dismiss_everything(session, today)
    session.commit()

    add_evidence(session, "t2", "IRS notice", received_at=NOW + timedelta(days=1))
    tomorrow = start_or_resume_review(
        session, load(), review_date=TOMORROW, now=NOW + timedelta(days=1)
    )

    assert tomorrow.run.id != today.run.id
    assert tomorrow.run.review_date == TOMORROW
    assert tomorrow.run.revision == 1
    assert tomorrow.run.state == RunState.NOT_STARTED


def test_a_restart_of_one_scope_leaves_another_scope_s_review_alone(
    session: Session,
) -> None:
    """Scope is part of a review's identity, so restarts do not cross it."""
    add_evidence(session, "t1")
    inbox = start(session)
    archive = start_or_resume_review(
        session, load(), now=NOW, scope=read_scope("archived")
    )

    restart_review(session, load(), now=NOW + timedelta(hours=1))

    assert session.get(ReviewRun, inbox.run.id).state == RunState.ABANDONED
    assert session.get(ReviewRun, archive.run.id).state != RunState.ABANDONED
