from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Sequence

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.capabilities.config import ActionKind, CapabilityConfig, LoadedCapabilities
from adminos.db.models import Evidence, ReviewDecision, ReviewItem
from adminos.domain.review import (
    Assessment,
    DecisionKind,
    DecisionRefused,
    GroupState,
    ItemState,
    ReviewNotFound,
    RunState,
    decide_group,
    read_group,
    read_group_items,
    record_assessment,
    record_decision,
    refresh_states,
    start_or_resume_review,
)
from tests.conftest import build_capability


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
TODAY = NOW.date()

TAXES = build_capability(
    key="financial_taxes",
    recommendation_policy={
        "version": "taxes.test",
        "categories": ["obligation", "reference"],
        "rules": [
            {
                "id": "kpmg_is_an_obligation",
                "when": {"subject_contains": ["KPMG"]},
                "recommend": "monday.create_task",
                "confidence": 0.9,
                "rationale": "The adviser is asking for something.",
                "aligns_with": ["financial_compliance"],
            }
        ],
    },
    objectives={"default_keys": ["financial_compliance"]},
)
ADMIN = build_capability(key="admin", labels=["Admin"], position=20)


def load(*capabilities: CapabilityConfig) -> LoadedCapabilities:
    return LoadedCapabilities(
        version="test.1",
        digest="d" * 64,
        channel="email",
        capabilities=capabilities or (TAXES,),
    )


@pytest.fixture
def session(tmp_path: Path) -> Session:
    url = f"sqlite:///{tmp_path / 'review.db'}"
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
    subject: str,
    capabilities: Sequence[str] = ("financial_taxes",),
    received_at: datetime | None = None,
    participants: Sequence[str] = ("cpa@kpmg.com",),
    content_hash: str | None = None,
) -> Evidence:
    evidence = Evidence(
        source_system="gmail",
        source_thread_id=thread_id,
        subject=subject,
        participants=list(participants),
        received_at=received_at or NOW,
        content_hash=content_hash or f"hash-{thread_id}",
        capability_keys=list(capabilities),
    )
    session.add(evidence)
    session.flush()
    return evidence


def start(session: Session, *capabilities: CapabilityConfig, now: datetime = NOW):  # noqa: ANN201
    return start_or_resume_review(session, load(*capabilities), now=now)


def test_a_review_creates_one_group_per_enabled_capability(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")

    view = start(session, TAXES, ADMIN)

    assert [group.group.capability_key for group in view.groups] == ["financial_taxes", "admin"]
    assert view.run.review_date == TODAY
    assert view.run.state == RunState.IN_PROGRESS


def test_groups_are_presented_in_configured_order(session: Session) -> None:
    """The order is data, so reordering the review is a configuration change."""
    first = build_capability(key="admin", labels=["Admin"], position=5)
    second = build_capability(key="financial_taxes", position=50)

    view = start(session, second, first)

    assert [group.group.capability_key for group in view.groups] == ["admin", "financial_taxes"]


def test_starting_twice_in_a_day_resumes_the_same_review(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    first = start(session, TAXES)
    session.commit()

    second = start(session, TAXES, now=NOW + timedelta(hours=3))

    assert second.run.id == first.run.id
    assert session.query(ReviewItem).count() == 1


def test_resuming_picks_up_mail_that_arrived_since(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    start(session, TAXES)
    session.commit()

    add_evidence(session, "t2", "IRS notice")
    view = start(session, TAXES, now=NOW + timedelta(hours=3))

    assert {item.source_thread_id for item in view.groups[0].items} == {"t1", "t2"}


def test_a_new_day_starts_a_new_review(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    first = start(session, TAXES)
    session.commit()

    tomorrow = start_or_resume_review(
        session, load(TAXES), review_date=date(2026, 7, 29), now=NOW + timedelta(days=1)
    )

    assert tomorrow.run.id != first.run.id


def test_evidence_reaches_only_the_capability_it_belongs_to(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities", capabilities=["financial_taxes"])
    add_evidence(session, "t2", "Renew the registration", capabilities=["admin"])

    view = start(session, TAXES, ADMIN)

    assert [item.source_thread_id for item in view.groups[0].items] == ["t1"]
    assert [item.source_thread_id for item in view.groups[1].items] == ["t2"]


def test_a_thread_in_two_capabilities_appears_in_both(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities", capabilities=["financial_taxes", "admin"])

    view = start(session, TAXES, ADMIN)

    assert all(len(group.items) == 1 for group in view.groups)


def test_a_matching_rule_supplies_the_recommendation(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")

    item = start(session, TAXES).groups[0].items[0]

    assert item.recommendation == ActionKind.MONDAY_CREATE_TASK
    assert item.recommendation_source == "policy"
    assert item.rule_id == "kpmg_is_an_obligation"
    assert item.recommendation_confidence == 0.9
    assert item.objective_keys == ["financial_compliance"]


def test_unmatched_mail_falls_to_needs_review(session: Session) -> None:
    """The default is deliberately not an action: silence must not act."""
    add_evidence(session, "t1", "Lunch on Thursday?")

    item = start(session, TAXES).groups[0].items[0]

    assert item.recommendation == "needs_review"
    assert item.recommendation_source == "default"
    assert item.recommendation_confidence == 0.0


def test_a_rule_can_match_on_sender_domain(session: Session) -> None:
    capability = build_capability(
        recommendation_policy={
            "version": "taxes.test",
            "categories": ["obligation"],
            "rules": [
                {
                    "id": "from_the_adviser",
                    "when": {"participant_domains": ["kpmg.com"]},
                    "recommend": "gmail.label",
                    "rationale": "Sender is the adviser.",
                }
            ],
        }
    )
    add_evidence(session, "t1", "Anything", participants=["CPA@KPMG.com"])

    item = start(session, capability).groups[0].items[0]

    assert item.recommendation == ActionKind.GMAIL_LABEL


def test_a_rule_can_match_on_age(session: Session) -> None:
    capability = build_capability(
        recommendation_policy={
            "version": "taxes.test",
            "categories": ["obligation"],
            "rules": [
                {
                    "id": "stale",
                    "when": {"older_than_days": 30},
                    "recommend": "gmail.archive",
                    "rationale": "Nothing has happened in a month.",
                }
            ],
        }
    )
    add_evidence(session, "old", "Old thread", received_at=NOW - timedelta(days=60))
    add_evidence(session, "new", "New thread", received_at=NOW - timedelta(days=1))

    items = {item.source_thread_id: item for item in start(session, capability).groups[0].items}

    assert items["old"].recommendation == ActionKind.GMAIL_ARCHIVE
    assert items["new"].recommendation == "needs_review"


def test_an_item_keeps_the_versions_that_produced_it(session: Session) -> None:
    """A decision is only explainable against the configuration of its day."""
    add_evidence(session, "t1", "KPMG Activities")

    view = start(session, TAXES)

    assert view.run.config_version == "test.1"
    assert view.groups[0].items[0].policy_version == "taxes.test"


def test_approving_records_the_action_but_does_not_execute_it(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)
    item = view.groups[0].items[0]

    decided = record_decision(session, TAXES, view.run, item, DecisionKind.APPROVE, now=NOW)

    assert decided.state == ItemState.APPROVED
    assert decided.approved_action == ActionKind.MONDAY_CREATE_TASK
    assert decided.decided_at == NOW


def test_approving_a_non_action_recommendation_is_refused(session: Session) -> None:
    add_evidence(session, "t1", "Lunch on Thursday?")
    view = start(session, TAXES)

    with pytest.raises(DecisionRefused, match="nothing to approve"):
        record_decision(session, TAXES, view.run, view.groups[0].items[0], DecisionKind.APPROVE)


def test_an_override_may_choose_a_different_permitted_action(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)

    decided = record_decision(
        session,
        TAXES,
        view.run,
        view.groups[0].items[0],
        DecisionKind.OVERRIDE,
        action=ActionKind.GMAIL_ARCHIVE,
    )

    assert decided.approved_action == ActionKind.GMAIL_ARCHIVE
    assert session.query(ReviewDecision).one().followed_recommendation is False


def test_an_override_to_a_forbidden_action_is_refused(session: Session) -> None:
    """Permission is configuration; no request phrasing can widen it."""
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)

    with pytest.raises(DecisionRefused, match="not allowed"):
        record_decision(
            session,
            TAXES,
            view.run,
            view.groups[0].items[0],
            DecisionKind.OVERRIDE,
            action=ActionKind.GMAIL_SEND_DRAFT,
        )


def test_an_override_without_an_action_is_refused(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)

    with pytest.raises(DecisionRefused, match="must name the action"):
        record_decision(session, TAXES, view.run, view.groups[0].items[0], DecisionKind.OVERRIDE)


def test_dismissing_settles_an_item_without_an_action(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)

    decided = record_decision(session, TAXES, view.run, view.groups[0].items[0], DecisionKind.DISMISS)

    assert decided.state == ItemState.DISMISSED
    assert decided.approved_action is None


def test_a_settled_item_cannot_be_decided_twice(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)
    item = view.groups[0].items[0]
    record_decision(session, TAXES, view.run, item, DecisionKind.DISMISS)

    with pytest.raises(DecisionRefused, match="already"):
        record_decision(session, TAXES, view.run, item, DecisionKind.APPROVE)


def test_every_decision_is_recorded(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)
    item = view.groups[0].items[0]

    record_decision(session, TAXES, view.run, item, DecisionKind.APPROVE, note="Do it")

    recorded = session.query(ReviewDecision).one()
    assert recorded.decision == DecisionKind.APPROVE
    assert recorded.action == ActionKind.MONDAY_CREATE_TASK
    assert recorded.followed_recommendation is True
    assert recorded.learning_scope == "capability"
    assert recorded.note == "Do it"


def test_a_capability_that_does_not_learn_keeps_no_note(session: Session) -> None:
    capability = build_capability(learning={"scope": "none", "record_decisions": False})
    add_evidence(session, "t1", "Anything")
    view = start(session, capability)

    record_decision(
        session,
        capability,
        view.run,
        view.groups[0].items[0],
        DecisionKind.DISMISS,
        note="Private",
    )

    assert session.query(ReviewDecision).one().note is None


def test_bulk_decisions_settle_a_whole_group(session: Session) -> None:
    add_evidence(session, "t1", "Newsletter one", capabilities=["admin"])
    add_evidence(session, "t2", "Newsletter two", capabilities=["admin"])
    view = start(session, ADMIN)

    decided = decide_group(session, ADMIN, view.run, view.groups[0].group, DecisionKind.DISMISS)

    assert [item.state for item in decided] == [ItemState.DISMISSED, ItemState.DISMISSED]


def test_bulk_decisions_can_name_specific_items(session: Session) -> None:
    add_evidence(session, "t1", "Newsletter one", capabilities=["admin"])
    add_evidence(session, "t2", "Newsletter two", capabilities=["admin"])
    view = start(session, ADMIN)
    chosen = view.groups[0].items[0]

    decide_group(
        session,
        ADMIN,
        view.run,
        view.groups[0].group,
        DecisionKind.DISMISS,
        item_ids=[chosen.id],
    )

    states = {item.id: item.state for item in read_group_items(session, view.groups[0].group)}
    assert states[chosen.id] == ItemState.DISMISSED
    assert list(states.values()).count(ItemState.PENDING) == 1


def test_bulk_decisions_are_refused_when_the_capability_forbids_them(session: Session) -> None:
    capability = build_capability(approval={"allow_bulk_decisions": False})
    add_evidence(session, "t1", "Anything")
    view = start(session, capability)

    with pytest.raises(DecisionRefused, match="bulk"):
        decide_group(
            session, capability, view.run, view.groups[0].group, DecisionKind.DISMISS
        )


def test_a_bulk_decision_naming_a_missing_item_is_refused(session: Session) -> None:
    add_evidence(session, "t1", "Anything")
    view = start(session, TAXES)

    with pytest.raises(ReviewNotFound):
        decide_group(
            session,
            TAXES,
            view.run,
            view.groups[0].group,
            DecisionKind.DISMISS,
            item_ids=["missing"],
        )


def test_a_group_completes_when_every_item_is_settled(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)
    record_decision(session, TAXES, view.run, view.groups[0].items[0], DecisionKind.DISMISS)

    refreshed = refresh_states(session, load(TAXES), view.run)

    assert refreshed.groups[0].group.state == GroupState.COMPLETED
    assert refreshed.run.state == RunState.COMPLETED


def test_an_approved_action_holds_the_group_open_until_it_runs(session: Session) -> None:
    """Completion means the work happened, not that the decision was made."""
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)
    record_decision(session, TAXES, view.run, view.groups[0].items[0], DecisionKind.APPROVE)

    refreshed = refresh_states(session, load(TAXES), view.run)

    assert refreshed.groups[0].group.state == GroupState.AWAITING_ACTIONS
    assert refreshed.run.state == RunState.AWAITING_ACTIONS


def test_a_partly_decided_group_is_in_progress(session: Session) -> None:
    add_evidence(session, "t1", "One", capabilities=["admin"])
    add_evidence(session, "t2", "Two", capabilities=["admin"])
    view = start(session, ADMIN)
    record_decision(session, ADMIN, view.run, view.groups[0].items[0], DecisionKind.DISMISS)

    refreshed = refresh_states(session, load(ADMIN), view.run)

    assert refreshed.groups[0].group.state == GroupState.IN_PROGRESS


def test_the_current_group_is_the_first_unfinished_one(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    add_evidence(session, "t2", "Renew the registration", capabilities=["admin"])
    view = start(session, TAXES, ADMIN)
    record_decision(session, TAXES, view.run, view.groups[0].items[0], DecisionKind.DISMISS)

    refreshed = refresh_states(session, load(TAXES, ADMIN), view.run)
    current = refreshed.current_group()

    assert current is not None
    assert current.group.capability_key == "admin"


def test_an_empty_group_is_already_complete(session: Session) -> None:
    view = start(session, TAXES)

    assert view.groups[0].group.state == GroupState.COMPLETED


def test_a_settled_thread_does_not_return_tomorrow(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    today = start(session, TAXES)
    record_decision(session, TAXES, today.run, today.groups[0].items[0], DecisionKind.DISMISS)
    session.commit()

    tomorrow = start_or_resume_review(
        session, load(TAXES), review_date=date(2026, 7, 29), now=NOW + timedelta(days=1)
    )

    assert tomorrow.groups[0].items == []


def test_a_reply_brings_a_settled_thread_back(session: Session) -> None:
    """Dismissal settles a conversation as it stood, not the conversation forever."""
    evidence = add_evidence(session, "t1", "KPMG Activities")
    today = start(session, TAXES)
    record_decision(session, TAXES, today.run, today.groups[0].items[0], DecisionKind.DISMISS)
    session.commit()

    evidence.content_hash = "hash-after-the-reply"
    session.flush()
    tomorrow = start_or_resume_review(
        session, load(TAXES), review_date=date(2026, 7, 29), now=NOW + timedelta(days=1)
    )

    assert len(tomorrow.groups[0].items) == 1


def test_review_items_outlive_the_evidence_they_came_from(session: Session) -> None:
    """Archiving a thread retires its evidence; the audit trail must survive it."""
    evidence = add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)
    record_decision(session, TAXES, view.run, view.groups[0].items[0], DecisionKind.DISMISS)
    session.commit()

    session.delete(evidence)
    session.commit()

    survivor = session.query(ReviewItem).one()
    assert survivor.subject == "KPMG Activities"
    assert survivor.state == ItemState.DISMISSED


def test_an_assessment_records_the_model_and_its_category(session: Session) -> None:
    add_evidence(session, "t1", "Lunch on Thursday?")
    view = start(session, TAXES)

    assessed = record_assessment(
        session,
        TAXES,
        view.groups[0].items[0],
        Assessment(
            category="reference",
            confidence=0.4,
            rationale="Background only.",
            model_version="gpt-test",
        ),
    )

    assert assessed.category == "reference"
    assert assessed.model_version == "gpt-test"


def test_an_unknown_category_is_refused(session: Session) -> None:
    add_evidence(session, "t1", "Anything")
    view = start(session, TAXES)

    with pytest.raises(DecisionRefused, match="not a category"):
        record_assessment(
            session,
            TAXES,
            view.groups[0].items[0],
            Assessment(
                category="invented",
                confidence=1.0,
                rationale="Made up.",
                model_version="gpt-test",
            ),
        )


def test_a_confident_model_recommendation_is_adopted(session: Session) -> None:
    add_evidence(session, "t1", "Lunch on Thursday?")
    view = start(session, TAXES)

    assessed = record_assessment(
        session,
        TAXES,
        view.groups[0].items[0],
        Assessment(
            category="obligation",
            confidence=0.95,
            rationale="They asked for a filing.",
            model_version="gpt-test",
            recommendation="monday.create_task",
        ),
    )

    assert assessed.recommendation == ActionKind.MONDAY_CREATE_TASK
    assert assessed.recommendation_source == "ai"


def test_an_unconfident_model_recommendation_is_recorded_but_not_adopted(session: Session) -> None:
    add_evidence(session, "t1", "Lunch on Thursday?")
    view = start(session, TAXES)

    assessed = record_assessment(
        session,
        TAXES,
        view.groups[0].items[0],
        Assessment(
            category="obligation",
            confidence=0.2,
            rationale="Possibly a filing.",
            model_version="gpt-test",
            recommendation="monday.create_task",
        ),
    )

    assert assessed.recommendation == "needs_review"
    assert assessed.provenance["unadopted_recommendation"] == "monday.create_task"


def test_the_model_may_not_recommend_a_forbidden_action(session: Session) -> None:
    add_evidence(session, "t1", "Anything")
    view = start(session, TAXES)

    with pytest.raises(DecisionRefused, match="not allowed"):
        record_assessment(
            session,
            TAXES,
            view.groups[0].items[0],
            Assessment(
                category="obligation",
                confidence=1.0,
                rationale="Send it.",
                model_version="gpt-test",
                recommendation="gmail.send_draft",
            ),
        )


def test_a_capability_may_refuse_model_recommendations_entirely(session: Session) -> None:
    capability = build_capability(
        recommendation_policy={
            "version": "taxes.test",
            "categories": ["obligation"],
            "allow_ai_recommendation": False,
        }
    )
    add_evidence(session, "t1", "Anything")
    view = start(session, capability)

    with pytest.raises(DecisionRefused, match="does not accept"):
        record_assessment(
            session,
            capability,
            view.groups[0].items[0],
            Assessment(
                category="obligation",
                confidence=1.0,
                rationale="Do it.",
                model_version="gpt-test",
                recommendation="gmail.archive",
            ),
        )


def test_a_capability_requiring_alignment_refuses_an_unaligned_action(session: Session) -> None:
    capability = build_capability(
        objectives={"default_keys": ["financial_compliance"], "require_alignment": True}
    )
    add_evidence(session, "t1", "Anything")
    view = start(session, capability)
    item = view.groups[0].items[0]
    item.objective_keys = []
    session.flush()

    with pytest.raises(DecisionRefused, match="objective"):
        record_decision(
            session,
            capability,
            view.run,
            item,
            DecisionKind.OVERRIDE,
            action=ActionKind.GMAIL_ARCHIVE,
        )


def test_a_capability_without_an_execute_step_cannot_approve_actions(session: Session) -> None:
    capability = build_capability(
        playbook={"id": "read_only", "steps": ["collect_evidence", "recommend", "await_decision"]}
    )
    add_evidence(session, "t1", "Anything")
    view = start(session, capability)

    with pytest.raises(DecisionRefused, match="execute_approved"):
        record_decision(
            session,
            capability,
            view.run,
            view.groups[0].items[0],
            DecisionKind.OVERRIDE,
            action=ActionKind.GMAIL_ARCHIVE,
        )


def test_a_group_can_be_read_back_by_capability(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)

    group = read_group(session, view.run, "financial_taxes")

    assert group.id == view.groups[0].group.id


def test_reading_a_group_that_does_not_exist_is_reported(session: Session) -> None:
    view = start(session, TAXES)

    with pytest.raises(ReviewNotFound):
        read_group(session, view.run, "career_advisor_calls")


def test_deferring_clears_an_item_out_of_today(session: Session) -> None:
    add_evidence(session, "t1", "KPMG Activities")
    view = start(session, TAXES)

    deferred = record_decision(session, TAXES, view.run, view.groups[0].items[0], DecisionKind.DEFER)
    refreshed = refresh_states(session, load(TAXES), view.run)

    assert deferred.state == ItemState.DEFERRED
    assert refreshed.groups[0].group.state == GroupState.COMPLETED


def test_a_deferred_thread_comes_back_tomorrow(session: Session) -> None:
    """Deferral is 'not today', which is a different answer from 'done'."""
    add_evidence(session, "t1", "KPMG Activities")
    today = start(session, TAXES)
    record_decision(session, TAXES, today.run, today.groups[0].items[0], DecisionKind.DEFER)
    session.commit()

    tomorrow = start_or_resume_review(
        session, load(TAXES), review_date=date(2026, 7, 29), now=NOW + timedelta(days=1)
    )

    assert len(tomorrow.groups[0].items) == 1


def test_a_bulk_decision_refused_for_one_item_changes_none(session: Session) -> None:
    """Bulk is one intent: it cannot half-apply because a later item is different."""
    add_evidence(session, "t1", "KPMG Activities")
    add_evidence(session, "t2", "Lunch on Thursday?")
    view = start(session, TAXES)

    with pytest.raises(DecisionRefused, match="nothing to approve"):
        decide_group(session, TAXES, view.run, view.groups[0].group, DecisionKind.APPROVE)

    states = {item.state for item in read_group_items(session, view.groups[0].group)}
    assert states == {ItemState.PENDING}


def test_a_bulk_decision_skips_items_already_settled(session: Session) -> None:
    add_evidence(session, "t1", "One", capabilities=["admin"])
    add_evidence(session, "t2", "Two", capabilities=["admin"])
    view = start(session, ADMIN)
    record_decision(session, ADMIN, view.run, view.groups[0].items[0], DecisionKind.DISMISS)

    decided = decide_group(session, ADMIN, view.run, view.groups[0].group, DecisionKind.DISMISS)

    assert len(decided) == 1


def test_a_group_waiting_on_execution_does_not_hold_up_the_review(session: Session) -> None:
    """Approving in the first group should move Brian on, not strand him."""
    add_evidence(session, "t1", "KPMG obligation")
    add_evidence(session, "t2", "Something admin", capabilities=["admin"])
    loaded = load(TAXES, ADMIN)
    view = start_or_resume_review(session, loaded, now=NOW)

    record_decision(
        session,
        TAXES,
        view.run,
        view.groups[0].items[0],
        DecisionKind.APPROVE,
    )
    refreshed = refresh_states(session, loaded, view.run)
    current = refreshed.current_group()

    assert refreshed.groups[0].group.state == GroupState.AWAITING_ACTIONS
    assert current is not None
    assert current.group.capability_key == "admin"


def test_outstanding_actions_are_what_is_left_once_every_group_is_decided(
    session: Session,
) -> None:
    add_evidence(session, "t1", "KPMG obligation")
    loaded = load(TAXES)
    view = start_or_resume_review(session, loaded, now=NOW)
    record_decision(
        session, TAXES, view.run, view.groups[0].items[0], DecisionKind.APPROVE
    )

    refreshed = refresh_states(session, loaded, view.run)
    current = refreshed.current_group()

    assert refreshed.run.state == RunState.AWAITING_ACTIONS
    assert current is not None
    assert current.group.state == GroupState.AWAITING_ACTIONS
