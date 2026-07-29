from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.capabilities.config import ActionKind, CapabilityConfig, LoadedCapabilities, MatchRule
from adminos.db.models import (
    CandidateRule,
    Evidence,
    LearningEvent,
    ReviewDecision,
    ReviewItem,
)
from adminos.domain.decisions import DecisionKind, ItemState
from adminos.domain.learning import LearningKind, read_learning_events
from adminos.domain.review import (
    LEARNED_SOURCE,
    RULE_ACTOR_PREFIX,
    record_decision,
    start_or_resume_review,
)
from adminos.domain.rules import (
    HUMAN_SOURCE,
    RuleRefused,
    RuleState,
    read_active_rules,
    record_rule,
    transition_rule,
)
from tests.conftest import build_capability


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
SENDER = "clerk@council.gov"
DOMAIN = "council.gov"

ADMIN = build_capability(key="admin", labels=["Admin"])
AUTOMATING_ADMIN = build_capability(
    key="admin",
    labels=["Admin"],
    learning={
        "scope": "capability",
        "record_decisions": True,
        "record_message_content": False,
        "allow_rule_learning": True,
        "allow_automatable_rules": True,
    },
)
NO_LEARNING = build_capability(
    key="admin",
    labels=["Admin"],
    learning={"scope": "none", "record_decisions": False, "record_message_content": False},
)
TRASHING_ADMIN = build_capability(
    key="admin",
    labels=["Admin"],
    allowed_actions=["gmail.archive", "gmail.trash"],
    execution={"permitted_actions": ["gmail.archive", "gmail.trash"]},
    learning={
        "scope": "capability",
        "record_decisions": True,
        "record_message_content": False,
        "allow_rule_learning": True,
        "allow_automatable_rules": True,
    },
)
FILING_ADMIN = build_capability(
    key="admin",
    labels=["Admin"],
    gmail={"labels": ["Admin"], "destinations": ["Later", "Notes"]},
    allowed_actions=["gmail.archive", "gmail.move"],
    execution={"permitted_actions": ["gmail.archive", "gmail.move"]},
    learning={
        "scope": "capability",
        "record_decisions": True,
        "record_message_content": False,
        "allow_rule_learning": True,
        "allow_automatable_rules": True,
    },
)
ARCHIVE_COUNCIL = MatchRule(participant_domains=[DOMAIN])


@pytest.fixture
def session(tmp_path: Path) -> Session:
    url = f"sqlite:///{tmp_path / 'learning.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


def add_evidence(session: Session, thread_id: str = "t1", capability_key: str = "admin") -> None:
    session.add(
        Evidence(
            source_system="gmail",
            source_thread_id=thread_id,
            subject="Renew the parking permit",
            participants=[SENDER],
            received_at=NOW,
            content_hash=f"hash-{thread_id}",
            capability_keys=[capability_key],
        )
    )
    session.flush()


def review(session: Session, capability: CapabilityConfig = ADMIN):  # noqa: ANN201
    return start_or_resume_review(
        session,
        LoadedCapabilities(
            version="test.1", digest="d" * 64, channel="email", capabilities=(capability,)
        ),
        now=NOW,
    )


def override(
    session: Session,
    capability: CapabilityConfig = ADMIN,
    action: ActionKind = ActionKind.GMAIL_ARCHIVE,
) -> ReviewItem:
    add_evidence(session, capability_key=capability.key)
    view = review(session, capability)
    item = view.groups[0].items[0]
    return record_decision(
        session, capability, view.run, item, DecisionKind.OVERRIDE, action=action, now=NOW
    )


def test_every_override_is_recorded_as_a_learning_event(session: Session) -> None:
    item = override(session)

    events = read_learning_events(session)

    assert len(events) == 1
    assert events[0].kind == LearningKind.OVERRIDE
    assert events[0].recommended == item.recommendation
    assert events[0].chosen == ActionKind.GMAIL_ARCHIVE


def test_a_learning_event_keeps_only_retained_metadata(session: Session) -> None:
    override(session)

    signals = read_learning_events(session)[0].signals or {}

    assert signals["participant_domains"] == [DOMAIN]
    assert signals["participants"] == [SENDER]
    assert set(signals) == {
        "subject",
        "participants",
        "participant_domains",
        "recommendation_source",
    }


def test_agreeing_is_recorded_too_so_disagreement_is_legible(session: Session) -> None:
    add_evidence(session)
    view = review(session)
    item = view.groups[0].items[0]

    record_decision(session, ADMIN, view.run, item, DecisionKind.DISMISS, now=NOW)

    assert read_learning_events(session)[0].kind == LearningKind.CONFIRMED_RECOMMENDATION


def test_a_capability_that_does_not_learn_records_nothing(session: Session) -> None:
    override(session, capability=NO_LEARNING)

    assert read_learning_events(session) == []


def test_a_correction_becomes_an_observation_not_a_rule(session: Session) -> None:
    override(session)

    rule = session.query(CandidateRule).one()

    assert rule.state == RuleState.OBSERVED
    assert rule.match_conditions == {"participant_domains": [DOMAIN]}
    assert read_active_rules(session, ADMIN) == []


def test_the_same_correction_twice_raises_one_observation(session: Session) -> None:
    override(session)
    add_evidence(session, thread_id="t2")
    view = review(session)
    second = [item for item in view.groups[0].items if item.source_thread_id == "t2"][0]
    record_decision(
        session,
        ADMIN,
        view.run,
        second,
        DecisionKind.OVERRIDE,
        action=ActionKind.GMAIL_ARCHIVE,
        now=NOW,
    )

    rule = session.query(CandidateRule).one()

    assert rule.observed_count == 2
    assert session.query(LearningEvent).count() == 2


def test_a_learning_event_points_at_the_rule_it_suggests(session: Session) -> None:
    override(session)

    event = read_learning_events(session)[0]

    assert event.candidate_rule_id == session.query(CandidateRule).one().id


def test_proposing_a_rule_does_not_activate_it(session: Session) -> None:
    rule = record_rule(
        session,
        ADMIN,
        ARCHIVE_COUNCIL,
        ActionKind.GMAIL_ARCHIVE,
        rationale="Council notices are read and archived.",
        state=RuleState.PROPOSED,
        source=HUMAN_SOURCE,
        actor="human",
        now=NOW,
    )

    assert rule.state == RuleState.PROPOSED
    assert read_active_rules(session, ADMIN) == []


def test_confirming_a_rule_makes_it_recommend(session: Session) -> None:
    rule = record_rule(
        session,
        ADMIN,
        ARCHIVE_COUNCIL,
        ActionKind.GMAIL_ARCHIVE,
        rationale="Council notices are read and archived.",
        state=RuleState.PROPOSED,
        source=HUMAN_SOURCE,
        actor="human",
        now=NOW,
    )
    transition_rule(session, ADMIN, rule, RuleState.CONFIRMED, actor="human", now=NOW)
    add_evidence(session)

    item = review(session).groups[0].items[0]

    assert item.recommendation == ActionKind.GMAIL_ARCHIVE
    assert item.recommendation_source == LEARNED_SOURCE
    assert item.rule_id == rule.id
    assert item.state == ItemState.PENDING


def test_confirming_does_not_permit_acting_without_approval(session: Session) -> None:
    rule = confirmed(session, AUTOMATING_ADMIN)
    add_evidence(session)

    item = review(session, AUTOMATING_ADMIN).groups[0].items[0]

    assert rule.state == RuleState.CONFIRMED
    assert item.state == ItemState.PENDING


def test_only_a_promoted_rule_approves_without_being_asked(session: Session) -> None:
    rule = confirmed(session, AUTOMATING_ADMIN)
    transition_rule(
        session, AUTOMATING_ADMIN, rule, RuleState.AUTOMATABLE, actor="human", now=NOW
    )
    add_evidence(session)

    item = review(session, AUTOMATING_ADMIN).groups[0].items[0]

    assert item.state == ItemState.APPROVED
    assert item.approved_action == ActionKind.GMAIL_ARCHIVE


def test_a_confirmed_trash_rule_does_not_empty_the_inbox_by_itself(session: Session) -> None:
    """Confirmation makes a rule recommend Trash; only promotion lets it act."""
    rule = record_rule(
        session,
        TRASHING_ADMIN,
        ARCHIVE_COUNCIL,
        ActionKind.GMAIL_TRASH,
        rationale="Council circulars are read and thrown away.",
        state=RuleState.PROPOSED,
        source=HUMAN_SOURCE,
        actor="human",
        now=NOW,
    )
    transition_rule(session, TRASHING_ADMIN, rule, RuleState.CONFIRMED, actor="human", now=NOW)
    add_evidence(session)

    item = review(session, TRASHING_ADMIN).groups[0].items[0]

    assert item.recommendation == ActionKind.GMAIL_TRASH
    assert item.state == ItemState.PENDING

    transition_rule(session, TRASHING_ADMIN, rule, RuleState.AUTOMATABLE, actor="human", now=NOW)
    add_evidence(session, thread_id="t2")
    items = review(session, TRASHING_ADMIN).groups[0].items
    promoted = [item for item in items if item.source_thread_id == "t2"][0]

    assert promoted.state == ItemState.APPROVED
    assert promoted.approved_action == ActionKind.GMAIL_TRASH


def test_a_correction_that_files_mail_remembers_the_folder(session: Session) -> None:
    """The lesson is "file this in Later", not "file this somewhere"."""
    add_evidence(session, capability_key=FILING_ADMIN.key)
    view = review(session, FILING_ADMIN)
    record_decision(
        session,
        FILING_ADMIN,
        view.run,
        view.groups[0].items[0],
        DecisionKind.OVERRIDE,
        action=ActionKind.GMAIL_MOVE,
        action_params={"label": "Later"},
        now=NOW,
    )

    rule = session.query(CandidateRule).one()

    assert rule.action == ActionKind.GMAIL_MOVE
    assert rule.action_params == {"label": "Later"}
    assert "into Later" in rule.rationale


def test_the_same_action_into_two_folders_is_two_observations(session: Session) -> None:
    """Two folders are two different lessons, however alike the mail looks."""
    add_evidence(session, capability_key=FILING_ADMIN.key)
    add_evidence(session, thread_id="t2", capability_key=FILING_ADMIN.key)
    view = review(session, FILING_ADMIN)
    for item, folder in zip(view.groups[0].items, ("Later", "Notes")):
        record_decision(
            session,
            FILING_ADMIN,
            view.run,
            item,
            DecisionKind.OVERRIDE,
            action=ActionKind.GMAIL_MOVE,
            action_params={"label": folder},
            now=NOW,
        )

    rules = session.query(CandidateRule).all()

    assert [rule.action_params for rule in rules] == [{"label": "Later"}, {"label": "Notes"}]


def test_a_confirmed_filing_rule_recommends_its_folder(session: Session) -> None:
    rule = record_rule(
        session,
        FILING_ADMIN,
        ARCHIVE_COUNCIL,
        ActionKind.GMAIL_MOVE,
        params={"label": "Later"},
        rationale="Council notices are worth keeping, out of the inbox.",
        state=RuleState.PROPOSED,
        source=HUMAN_SOURCE,
        actor="human",
        now=NOW,
    )
    transition_rule(session, FILING_ADMIN, rule, RuleState.CONFIRMED, actor="human", now=NOW)
    add_evidence(session)

    item = review(session, FILING_ADMIN).groups[0].items[0]

    assert item.recommendation == ActionKind.GMAIL_MOVE
    assert item.recommendation_params == {"label": "Later"}


def test_a_rule_filing_mail_somewhere_the_capability_does_not_is_refused(
    session: Session,
) -> None:
    with pytest.raises(RuleRefused, match="not usable"):
        record_rule(
            session,
            FILING_ADMIN,
            ARCHIVE_COUNCIL,
            ActionKind.GMAIL_MOVE,
            params={"label": "Career/Citi"},
            rationale="Worth keeping.",
            state=RuleState.PROPOSED,
            source=HUMAN_SOURCE,
            actor="human",
            now=NOW,
        )


def test_a_rule_approval_says_it_was_a_rule(session: Session) -> None:
    rule = confirmed(session, AUTOMATING_ADMIN)
    transition_rule(
        session, AUTOMATING_ADMIN, rule, RuleState.AUTOMATABLE, actor="human", now=NOW
    )
    add_evidence(session)
    review(session, AUTOMATING_ADMIN)

    decision = session.query(ReviewDecision).one()

    assert decision.actor == f"{RULE_ACTOR_PREFIX}{rule.id}"


def test_a_capability_that_forbids_automation_refuses_promotion(session: Session) -> None:
    rule = confirmed(session, ADMIN)

    with pytest.raises(RuleRefused) as error:
        transition_rule(session, ADMIN, rule, RuleState.AUTOMATABLE, actor="human", now=NOW)

    assert "act without approval" in str(error.value)


def test_a_rule_cannot_skip_confirmation(session: Session) -> None:
    rule = record_rule(
        session,
        AUTOMATING_ADMIN,
        ARCHIVE_COUNCIL,
        ActionKind.GMAIL_ARCHIVE,
        rationale="Council notices are read and archived.",
        state=RuleState.PROPOSED,
        source=HUMAN_SOURCE,
        actor="human",
        now=NOW,
    )

    with pytest.raises(RuleRefused) as error:
        transition_rule(
            session, AUTOMATING_ADMIN, rule, RuleState.AUTOMATABLE, actor="human", now=NOW
        )

    assert "cannot become 'automatable'" in str(error.value)


def test_promotion_can_be_withdrawn_without_discarding_the_rule(session: Session) -> None:
    rule = confirmed(session, AUTOMATING_ADMIN)
    transition_rule(
        session, AUTOMATING_ADMIN, rule, RuleState.AUTOMATABLE, actor="human", now=NOW
    )

    withdrawn = transition_rule(
        session, AUTOMATING_ADMIN, rule, RuleState.CONFIRMED, actor="human", now=NOW
    )

    assert withdrawn.state == RuleState.CONFIRMED
    assert withdrawn.automatable_at is None


def test_a_retired_rule_stops_recommending(session: Session) -> None:
    rule = confirmed(session, ADMIN)
    transition_rule(
        session, ADMIN, rule, RuleState.RETIRED, actor="human", reason="No longer true.", now=NOW
    )
    add_evidence(session)

    item = review(session).groups[0].items[0]

    assert read_active_rules(session, ADMIN) == []
    assert item.recommendation_source != LEARNED_SOURCE


def test_a_retired_rule_is_final(session: Session) -> None:
    rule = confirmed(session, ADMIN)
    transition_rule(session, ADMIN, rule, RuleState.RETIRED, actor="human", now=NOW)

    with pytest.raises(RuleRefused):
        transition_rule(session, ADMIN, rule, RuleState.CONFIRMED, actor="human", now=NOW)


def test_a_rule_with_no_conditions_cannot_be_expressed() -> None:
    """A rule that matches everything is refused where rules are defined."""
    with pytest.raises(ValidationError) as error:
        MatchRule()

    assert "would match everything" in str(error.value)


def test_a_rule_for_an_action_the_capability_may_not_take_is_refused(session: Session) -> None:
    limited = build_capability(
        key="admin",
        labels=["Admin"],
        allowed_actions=["gmail.archive"],
        execution={"permitted_actions": ["gmail.archive"]},
    )

    with pytest.raises(RuleRefused) as error:
        record_rule(
            session,
            limited,
            ARCHIVE_COUNCIL,
            ActionKind.GMAIL_LABEL,
            rationale="Label it.",
            state=RuleState.PROPOSED,
            source=HUMAN_SOURCE,
            actor="human",
            now=NOW,
        )

    assert "not allowed to 'gmail.label'" in str(error.value)


def test_learning_stays_inside_the_capability_that_learned_it(session: Session) -> None:
    confirmed(session, ADMIN)
    taxes = build_capability(key="financial_taxes")

    assert read_active_rules(session, taxes) == []
    assert len(read_active_rules(session, ADMIN)) == 1


def confirmed(session: Session, capability: CapabilityConfig) -> CandidateRule:
    rule = record_rule(
        session,
        capability,
        ARCHIVE_COUNCIL,
        ActionKind.GMAIL_ARCHIVE,
        rationale="Council notices are read and archived.",
        state=RuleState.PROPOSED,
        source=HUMAN_SOURCE,
        actor="human",
        now=NOW,
    )
    return transition_rule(session, capability, rule, RuleState.CONFIRMED, actor="human", now=NOW)
