"""A rule is tried before it is agreed to, and trying it changes nothing.

What these hold to: a preview reads mail and writes no mail, it says what it
matched and what it nearly matched, a match is a recommendation and never an
approval, and a rule reaches `tested` only by actually being run against
something.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.capabilities.config import CapabilityConfig, LoadedCapabilities
from adminos.db.models import Evidence
from adminos.domain.conditions import Condition, ConditionGroup, Operator
from adminos.domain.rule_testing import (
    PreviewSource,
    preview_draft,
    preview_rule,
)
from adminos.domain.rulebook import Effect, EffectKind, RuleDraft, RuleStatus, RuleType
from adminos.domain.rulebook_store import (
    move_rule,
    propose_rule,
    read_rule,
    read_rule_events,
)
from tests.conftest import build_capability


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)

TRANSFERS = build_capability(
    key="financial_taxes",
    allowed_actions=["gmail.label", "gmail.archive", "gmail.move"],
    execution={"permitted_actions": ["gmail.label", "gmail.archive"]},
    gmail={"labels": ["financial/taxes"], "destinations": ["Financial/Taxes", "Later"]},
)
ADMIN = build_capability(key="admin", labels=("Admin",), position=20)

TRANSFER_PATTERN = (
    r"^Inter Institution Transfer Request (?P<number>\d+) "
    r"Will Occur in (?P<days>\d+) Days$"
)
TRANSFER_SUBJECT = "Inter Institution Transfer Request 207960765 Will Occur in 3 Days"


def load() -> LoadedCapabilities:
    capabilities: tuple[CapabilityConfig, ...] = (TRANSFERS, ADMIN)
    return LoadedCapabilities(
        version="test.1", digest="d" * 64, channel="email", capabilities=capabilities
    )


@pytest.fixture
def session(tmp_path: Path) -> Session:
    url = f"sqlite:///{tmp_path / 'rule-testing.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


def remember(
    session: Session,
    thread_id: str,
    subject: str,
    *,
    labels: list[str] | None = None,
    groups: list[str] | None = None,
    sender: str = "alerts@fidelity.example",
    days_ago: int = 1,
) -> Evidence:
    evidence = Evidence(
        source_system="gmail",
        source_thread_id=thread_id,
        subject=subject,
        participants=[sender],
        received_at=NOW - timedelta(days=days_ago),
        snippet="A transfer is scheduled.",
        capability_keys=groups if groups is not None else ["financial_taxes"],
        label_ids=labels if labels is not None else ["INBOX"],
    )
    session.add(evidence)
    session.flush()
    return evidence


def transfer_draft(**overrides: object) -> RuleDraft:
    definition: dict[str, object] = {
        "name": "Inter-institution transfer notifications",
        "rule_type": RuleType.EMAIL_FILING,
        "capability_key": "financial_taxes",
        "match": ConditionGroup(
            all=[Condition(field="subject", operator=Operator.REGEX, value=TRANSFER_PATTERN)]
        ),
        "effects": [
            Effect(
                kind=EffectKind.RECOMMEND_ACTION,
                action="gmail.move",
                params={"label": "Financial/Taxes"},
            )
        ],
    }
    definition.update(overrides)
    return RuleDraft.model_validate(definition)


def test_a_preview_says_what_it_matched_and_why(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)
    remember(session, "t2", "Your July statement is ready")

    report = preview_draft(
        session, draft=transfer_draft(), source=PreviewSource.CURRENT_SNAPSHOT, now=NOW
    )

    assert report.counts() == {
        "considered": 2,
        "matched": 1,
        "unmatched": 1,
        "false_positive_candidates": 0,
        "false_negative_candidates": 0,
    }
    matched = report.matched[0]
    assert matched.reference == "t1"
    assert matched.captured == {"number": "207960765", "days": "3"}
    assert "the subject matches the pattern" in matched.reasons[0]


def test_a_match_is_a_recommendation_and_never_an_approval(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)

    report = preview_draft(
        session, draft=transfer_draft(), source=PreviewSource.CURRENT_SNAPSHOT, now=NOW
    )

    assert report.executed is False
    assert report.matched[0].requires_confirmation is True
    assert "recommend filing it in 'Financial/Taxes'" in report.matched[0].effects[0]


def test_a_preview_writes_no_mail_and_no_decision(session: Session) -> None:
    evidence = remember(session, "t1", TRANSFER_SUBJECT)
    before = (evidence.label_ids, evidence.capability_keys)

    preview_draft(session, draft=transfer_draft(), source=PreviewSource.CURRENT_SNAPSHOT, now=NOW)

    assert (evidence.label_ids, evidence.capability_keys) == before


def test_a_notification_says_what_it_would_say(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)
    draft = transfer_draft(
        rule_type=RuleType.EMAIL_NOTIFICATION,
        effects=[
            Effect(
                kind=EffectKind.SHOW_NOTIFICATION,
                message="Transfer {{number}} lands in {{days}} days",
            )
        ],
    )

    report = preview_draft(session, draft=draft, source=PreviewSource.CURRENT_SNAPSHOT, now=NOW)

    assert "Transfer 207960765 lands in 3 days" in report.matched[0].effects[0]


def test_the_inbox_snapshot_is_the_mail_still_in_the_inbox(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT, labels=["TRASH"])
    remember(session, "t2", TRANSFER_SUBJECT.replace("207960765", "207960766"))

    report = preview_draft(
        session, draft=transfer_draft(), source=PreviewSource.CURRENT_SNAPSHOT, now=NOW
    )

    assert [found.reference for found in report.matched] == ["t2"]
    assert report.sampled_from == "1 threads in the inbox as it stands"


def test_history_reaches_past_the_inbox(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT, labels=["TRASH"], days_ago=30)

    report = preview_draft(
        session, draft=transfer_draft(), source=PreviewSource.HISTORICAL_SAMPLE, now=NOW
    )

    assert [found.reference for found in report.matched] == ["t1"]


def test_mail_older_than_the_sample_is_left_out(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT, days_ago=400)

    report = preview_draft(
        session, draft=transfer_draft(), source=PreviewSource.HISTORICAL_SAMPLE, now=NOW
    )

    assert report.considered == 0


def test_named_threads_are_the_only_ones_tried(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)
    remember(session, "t2", TRANSFER_SUBJECT.replace("3 Days", "5 Days"))

    report = preview_draft(
        session,
        draft=transfer_draft(),
        source=PreviewSource.SELECTED_ITEMS,
        thread_ids=["t2"],
        now=NOW,
    )

    assert report.considered == 1
    assert [found.reference for found in report.matched] == ["t2"]


def test_a_typed_example_is_reported_as_typed(session: Session) -> None:
    report = preview_draft(
        session,
        draft=transfer_draft(),
        source=PreviewSource.SYNTHETIC_EXAMPLES,
        subjects=[TRANSFER_SUBJECT, "Lunch on Thursday"],
        now=NOW,
    )

    assert report.counts()["matched"] == 1
    assert any("not mail that arrived" in warning for warning in report.warnings)


def test_a_rule_that_catches_nothing_is_said_to_catch_nothing(session: Session) -> None:
    remember(session, "t1", "Your July statement is ready")

    report = preview_draft(
        session, draft=transfer_draft(), source=PreviewSource.CURRENT_SNAPSHOT, now=NOW
    )

    assert any("matched nothing in the sample" in warning for warning in report.warnings)


def test_a_rule_that_catches_most_of_the_sample_is_said_to(session: Session) -> None:
    for index in range(4):
        remember(session, f"t{index}", f"Transfer notice {index}")

    draft = transfer_draft(
        match=ConditionGroup(
            all=[Condition(field="subject", operator=Operator.CONTAINS, value="Transfer notice")]
        )
    )
    report = preview_draft(session, draft=draft, source=PreviewSource.CURRENT_SNAPSHOT, now=NOW)

    assert any("most of the sample" in warning for warning in report.warnings)


def test_an_empty_sample_is_not_a_test(session: Session) -> None:
    report = preview_draft(
        session, draft=transfer_draft(), source=PreviewSource.CURRENT_SNAPSHOT, now=NOW
    )

    assert any("nothing to try this against" in warning for warning in report.warnings)


def test_a_rule_that_misses_its_own_example_says_so(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)
    draft = transfer_draft(positive_examples=["Transfer request will occur shortly"])

    report = preview_draft(session, draft=draft, source=PreviewSource.CURRENT_SNAPSHOT, now=NOW)

    assert any("does not match its own example" in warning for warning in report.warnings)


def test_a_rule_that_matches_what_it_was_told_not_to_says_so(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)
    draft = transfer_draft(negative_examples=[TRANSFER_SUBJECT])

    report = preview_draft(session, draft=draft, source=PreviewSource.CURRENT_SNAPSHOT, now=NOW)

    assert any("something it was told not to" in warning for warning in report.warnings)


def test_a_match_in_someone_elses_group_is_offered_as_a_false_positive(
    session: Session,
) -> None:
    remember(session, "t1", TRANSFER_SUBJECT, groups=["admin"])

    report = preview_draft(
        session, draft=transfer_draft(), source=PreviewSource.CURRENT_SNAPSHOT, now=NOW
    )

    assert report.false_positives == (
        f"{TRANSFER_SUBJECT} — alerts@fidelity.example — reviewed as admin",
    )


def test_an_item_missed_by_one_condition_is_offered_as_a_false_negative(
    session: Session,
) -> None:
    remember(session, "t1", TRANSFER_SUBJECT, sender="alerts@vanguard.example")

    draft = transfer_draft(
        match=ConditionGroup(
            all=[
                Condition(field="subject", operator=Operator.REGEX, value=TRANSFER_PATTERN),
                Condition(
                    field="participant_domain",
                    operator=Operator.EQUALS,
                    value="fidelity.example",
                ),
            ]
        )
    )
    report = preview_draft(session, draft=draft, source=PreviewSource.CURRENT_SNAPSHOT, now=NOW)

    assert len(report.false_negatives) == 1
    assert "fidelity.example" in report.false_negatives[0]


def test_testing_a_rule_is_what_makes_it_tested(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)
    record = propose_rule(session, draft=transfer_draft(), loaded=load(), now=NOW)

    tested, report = preview_rule(
        session, rule_id=record.rule.id, source=PreviewSource.CURRENT_SNAPSHOT, now=NOW
    )

    assert tested.rule.status == RuleStatus.TESTED
    assert report.rule_id == record.rule.id
    assert report.version_number == 1


def test_a_test_is_kept_with_what_it_found(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)
    record = propose_rule(session, draft=transfer_draft(), loaded=load(), now=NOW)

    _, report = preview_rule(
        session, rule_id=record.rule.id, source=PreviewSource.CURRENT_SNAPSHOT, now=NOW
    )

    kept = read_rule_events(session, record.rule.id)[-1]
    assert kept.kind == "tested"
    assert kept.detail is not None
    assert kept.detail["test_run_id"] == report.test_run_id
    assert kept.detail["counts"]["matched"] == 1


def test_testing_an_active_rule_leaves_it_active(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)
    record = propose_rule(session, draft=transfer_draft(), loaded=load(), now=NOW)
    for status in (RuleStatus.TESTED, RuleStatus.CONFIRMED, RuleStatus.ACTIVE):
        move_rule(session, rule_id=record.rule.id, to=status, now=NOW)

    tested, _ = preview_rule(
        session, rule_id=record.rule.id, source=PreviewSource.CURRENT_SNAPSHOT, now=NOW
    )

    assert tested.rule.status == RuleStatus.ACTIVE


def test_a_test_is_of_the_version_that_would_be_confirmed(session: Session) -> None:
    remember(session, "t1", TRANSFER_SUBJECT)
    record = propose_rule(session, draft=transfer_draft(), loaded=load(), now=NOW)
    preview_rule(session, rule_id=record.rule.id, source=PreviewSource.CURRENT_SNAPSHOT, now=NOW)

    assert read_rule(session, record.rule.id).rule.status == RuleStatus.TESTED
