"""A rule is a record with versions, and a standing it has to earn.

What these hold to: a rule does one thing, an edit is a new version rather than
a change to the old one, agreeing with a rule is not the same act as putting it
to work, a rule cannot ask for permission that does not exist, and a rule type
nothing here can carry out is refused rather than stored.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.capabilities.config import CapabilityConfig, LoadedCapabilities
from adminos.domain.conditions import Condition, ConditionGroup, Operator
from adminos.domain.rulebook import (
    RULE_TYPES,
    Constraints,
    Effect,
    EffectClass,
    EffectKind,
    RuleDraft,
    RuleError,
    RuleStatus,
    RuleType,
)
from adminos.domain.rulebook_store import (
    RuleNotFound,
    amend_rule,
    move_rule,
    propose_rule,
    read_effective_rules,
    read_rule,
    read_rule_events,
    read_rule_versions,
    read_rules,
)
from tests.conftest import build_capability


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)

TRANSFERS = build_capability(
    key="financial_taxes",
    allowed_actions=["gmail.label", "gmail.archive", "gmail.move", "gmail.draft_reply"],
    execution={"permitted_actions": ["gmail.label", "gmail.archive"]},
    gmail={"labels": ["financial/taxes"], "destinations": ["Financial/Taxes", "Later"]},
)
ADMIN = build_capability(key="admin", labels=("Admin",), position=20)

TRANSFER_PATTERN = (
    r"^Inter Institution Transfer Request (?P<number>\d+) "
    r"Will Occur in (?P<days>\d+) Days$"
)


def load() -> LoadedCapabilities:
    capabilities: tuple[CapabilityConfig, ...] = (TRANSFERS, ADMIN)
    return LoadedCapabilities(
        version="test.1", digest="d" * 64, channel="email", capabilities=capabilities
    )


@pytest.fixture
def session(tmp_path: Path) -> Session:
    url = f"sqlite:///{tmp_path / 'rulebook.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


def transfer_match() -> ConditionGroup:
    return ConditionGroup(
        all=[Condition(field="subject", operator=Operator.REGEX, value=TRANSFER_PATTERN)]
    )


def filing_draft(**overrides: object) -> RuleDraft:
    definition: dict[str, object] = {
        "name": "Inter-institution transfer notifications",
        "description": "Files transfer notices where the record of them belongs.",
        "rule_type": RuleType.EMAIL_FILING,
        "capability_key": "financial_taxes",
        "priority": 200,
        "match": transfer_match(),
        "effects": [
            Effect(
                kind=EffectKind.RECOMMEND_ACTION,
                action="gmail.move",
                params={"label": "Financial/Taxes"},
            )
        ],
        "change_reason": "Brian named a recurring transfer notice.",
    }
    definition.update(overrides)
    return RuleDraft.model_validate(definition)


def test_a_proposed_rule_does_nothing(session: Session) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)

    assert record.rule.status == RuleStatus.PROPOSED
    assert record.version.number == 1
    assert read_effective_rules(session) == []


def test_the_rule_reads_back_as_the_sentences_it_was_written_as(session: Session) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)

    assert record.version.summary == [
        "Rule: Inter-institution transfer notifications",
        "When:",
        "- the subject matches the pattern Inter Institution Transfer Request "
        "[number] Will Occur in [days] Days",
        "Then:",
        "- recommend filing it in 'Financial/Taxes'",
        "- and nothing happens to the mailbox until you decide, the exact action is "
        "prepared, and you confirm it",
    ]


def test_a_rule_only_recommends_once_it_has_been_tested_confirmed_and_activated(
    session: Session,
) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)
    rule_id = record.rule.id

    for wanted in (RuleStatus.TESTED, RuleStatus.CONFIRMED):
        move_rule(session, rule_id=rule_id, to=wanted, now=NOW)
        assert read_effective_rules(session) == []

    move_rule(session, rule_id=rule_id, to=RuleStatus.ACTIVE, now=NOW)

    assert [found.rule.id for found in read_effective_rules(session)] == [rule_id]
    assert read_rule(session, rule_id).rule.activated_at == NOW


def test_confirming_a_rule_nobody_previewed_is_refused(session: Session) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)

    with pytest.raises(RuleError) as raised:
        move_rule(session, rule_id=record.rule.id, to=RuleStatus.CONFIRMED, now=NOW)

    assert "cannot become confirmed" in str(raised.value)


def test_editing_a_working_rule_stands_it_down_until_it_is_agreed_to_again(
    session: Session,
) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)
    rule_id = record.rule.id
    for wanted in (RuleStatus.TESTED, RuleStatus.CONFIRMED, RuleStatus.ACTIVE):
        move_rule(session, rule_id=rule_id, to=wanted, now=NOW)

    amended = amend_rule(
        session,
        rule_id=rule_id,
        draft=filing_draft(
            effects=[
                Effect(
                    kind=EffectKind.RECOMMEND_ACTION, action="gmail.move", params={"label": "Later"}
                )
            ],
            change_reason="Later, not Financial/Taxes.",
        ),
        loaded=load(),
        now=NOW,
    )

    assert amended.rule.status == RuleStatus.PROPOSED
    assert amended.rule.confirmed_at is None
    assert amended.rule.activated_at is None
    assert read_effective_rules(session) == []


def test_the_version_that_was_working_is_still_readable_afterwards(session: Session) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)
    rule_id = record.rule.id

    amend_rule(
        session,
        rule_id=rule_id,
        draft=filing_draft(
            effects=[
                Effect(
                    kind=EffectKind.RECOMMEND_ACTION, action="gmail.move", params={"label": "Later"}
                )
            ]
        ),
        loaded=load(),
        now=NOW,
    )

    versions = read_rule_versions(session, rule_id)
    assert [version.number for version in versions] == [1, 2]
    assert versions[0].effects == [
        {
            "kind": "recommend_action",
            "action": "gmail.move",
            "params": {"label": "Financial/Taxes"},
            "group_key": None,
            "message": None,
        }
    ]
    assert versions[1].supersedes_version_id == versions[0].id


def test_every_move_a_rule_made_is_kept_with_who_made_it(session: Session) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), actor="brian", now=NOW)
    rule_id = record.rule.id
    for wanted in (RuleStatus.TESTED, RuleStatus.CONFIRMED, RuleStatus.ACTIVE, RuleStatus.PAUSED):
        move_rule(session, rule_id=rule_id, to=wanted, actor="brian", now=NOW)

    events = read_rule_events(session, rule_id)

    assert [event.kind for event in events] == [
        "proposed",
        "tested",
        "confirmed",
        "activated",
        "paused",
    ]
    assert {event.actor for event in events} == {"brian"}


def test_a_paused_rule_comes_back_as_resumed_rather_than_activated_again(
    session: Session,
) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)
    rule_id = record.rule.id
    for wanted in (RuleStatus.TESTED, RuleStatus.CONFIRMED, RuleStatus.ACTIVE, RuleStatus.PAUSED):
        move_rule(session, rule_id=rule_id, to=wanted, now=NOW)

    move_rule(session, rule_id=rule_id, to=RuleStatus.ACTIVE, now=NOW)

    assert read_rule_events(session, rule_id)[-1].kind == "resumed"
    assert read_rule(session, rule_id).rule.paused_at is None


def test_a_retired_rule_stays_retired(session: Session) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)
    rule_id = record.rule.id
    move_rule(session, rule_id=rule_id, to=RuleStatus.RETIRED, reason="Not a thing.", now=NOW)

    with pytest.raises(RuleError) as raised:
        move_rule(session, rule_id=rule_id, to=RuleStatus.ACTIVE, now=NOW)

    assert "this is final" in str(raised.value)

    with pytest.raises(RuleError):
        amend_rule(session, rule_id=rule_id, draft=filing_draft(), loaded=load(), now=NOW)


def test_the_same_rule_written_twice_is_refused(session: Session) -> None:
    propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)

    with pytest.raises(RuleError) as raised:
        propose_rule(
            session,
            draft=filing_draft(name="Transfers, again", priority=400),
            loaded=load(),
            now=NOW,
        )

    assert "already says" in str(raised.value)


def test_the_rules_that_shape_a_review_come_in_priority_order(session: Session) -> None:
    for priority, label in ((400, "Later"), (100, "Financial/Taxes")):
        record = propose_rule(
            session,
            draft=filing_draft(
                name=f"Transfers to {label}",
                priority=priority,
                effects=[
                    Effect(
                        kind=EffectKind.RECOMMEND_ACTION,
                        action="gmail.move",
                        params={"label": label},
                    )
                ],
            ),
            loaded=load(),
            now=NOW,
        )
        for wanted in (RuleStatus.TESTED, RuleStatus.CONFIRMED, RuleStatus.ACTIVE):
            move_rule(session, rule_id=record.rule.id, to=wanted, now=NOW)

    assert [found.version.priority for found in read_effective_rules(session)] == [100, 400]


def test_rules_can_be_read_by_type_and_by_standing(session: Session) -> None:
    filing = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)
    propose_rule(
        session,
        draft=filing_draft(
            name="Transfers are financial",
            rule_type=RuleType.EMAIL_CLASSIFICATION,
            effects=[Effect(kind=EffectKind.ASSIGN_EMAIL_GROUP, group_key="financial_taxes")],
        ),
        loaded=load(),
        now=NOW,
    )
    move_rule(session, rule_id=filing.rule.id, to=RuleStatus.TESTED, now=NOW)

    assert len(read_rules(session, rule_type=RuleType.EMAIL_FILING)) == 1
    assert len(read_rules(session, status=RuleStatus.PROPOSED)) == 1
    assert len(read_rules(session, capability_key="financial_taxes")) == 2
    assert read_rules(session, capability_key="admin") == []


def test_a_stored_rule_reads_back_as_the_rule_that_was_written(session: Session) -> None:
    written = filing_draft()
    propose_rule(session, draft=written, loaded=load(), now=NOW)

    read_back = read_rules(session)[0].draft()

    assert read_back == written


def test_an_unknown_rule_is_a_missing_rule(session: Session) -> None:
    with pytest.raises(RuleNotFound):
        read_rule(session, "nothing")


def test_a_rule_does_one_thing() -> None:
    with pytest.raises(ValidationError) as raised:
        filing_draft(
            effects=[
                Effect(
                    kind=EffectKind.RECOMMEND_ACTION,
                    action="gmail.move",
                    params={"label": "Later"},
                ),
                Effect(
                    kind=EffectKind.RECOMMEND_ACTION,
                    action="gmail.archive",
                ),
            ]
        )

    assert "Two effects of the same kind are two rules" in str(raised.value)


def test_an_effect_a_type_does_not_own_is_refused() -> None:
    with pytest.raises(ValidationError) as raised:
        filing_draft(
            rule_type=RuleType.EMAIL_CLASSIFICATION,
            effects=[
                Effect(
                    kind=EffectKind.RECOMMEND_ACTION,
                    action="gmail.move",
                    params={"label": "Later"},
                )
            ],
        )

    assert "does not recommend_action" in str(raised.value)


def test_a_rule_type_nothing_here_can_carry_out_is_refused() -> None:
    with pytest.raises(ValidationError) as raised:
        filing_draft(rule_type=RuleType.TODO_REMINDER)

    assert "wakes up on a schedule" in str(raised.value)


def test_every_rule_type_is_declared_available_or_says_why_not() -> None:
    for spec in RULE_TYPES.values():
        assert spec.available is bool(spec.effects)
        assert spec.available or spec.unavailable_because


def test_filing_mail_without_naming_the_folder_is_refused() -> None:
    with pytest.raises(ValidationError) as raised:
        Effect(kind=EffectKind.RECOMMEND_ACTION, action="gmail.move")

    assert "naming the folder" in str(raised.value)


def test_a_folder_the_capability_does_not_have_is_refused(session: Session) -> None:
    with pytest.raises(RuleError) as raised:
        propose_rule(
            session,
            draft=filing_draft(
                effects=[
                    Effect(
                        kind=EffectKind.RECOMMEND_ACTION,
                        action="gmail.move",
                        params={"label": "Carrer/Citi"},
                    )
                ]
            ),
            loaded=load(),
            now=NOW,
        )

    assert "is not one of 'financial_taxes'" in str(raised.value)


def test_an_action_the_capability_may_not_take_is_refused(session: Session) -> None:
    with pytest.raises(RuleError) as raised:
        propose_rule(
            session,
            draft=filing_draft(
                capability_key="admin",
                effects=[Effect(kind=EffectKind.RECOMMEND_ACTION, action="gmail.move",
                                params={"label": "Later"})],
            ),
            loaded=load(),
            now=NOW,
        )

    assert "not allowed to gmail.move" in str(raised.value)


def test_a_group_the_review_does_not_have_is_refused(session: Session) -> None:
    with pytest.raises(RuleError) as raised:
        propose_rule(
            session,
            draft=filing_draft(
                rule_type=RuleType.EMAIL_CLASSIFICATION,
                effects=[Effect(kind=EffectKind.ASSIGN_EMAIL_GROUP, group_key="legal")],
            ),
            loaded=load(),
            now=NOW,
        )

    assert "is not a group in this review" in str(raised.value)


def test_a_rule_that_recommends_belongs_to_a_capability(session: Session) -> None:
    with pytest.raises(RuleError) as raised:
        propose_rule(session, draft=filing_draft(capability_key=None), loaded=load(), now=NOW)

    assert "belongs to a capability" in str(raised.value)


def test_a_rule_matching_everything_is_refused_where_it_is_written(session: Session) -> None:
    with pytest.raises(ValueError) as raised:
        propose_rule(
            session,
            draft=filing_draft(
                match=ConditionGroup(
                    all=[
                        Condition(
                            field="gmail_label", operator=Operator.EQUALS, value="INBOX"
                        )
                    ]
                )
            ),
            loaded=load(),
            now=NOW,
        )

    assert "narrow" in str(raised.value)


def test_a_rule_cannot_claim_to_act_on_its_own() -> None:
    with pytest.raises(ValidationError) as raised:
        Constraints(auto_execute=True)

    assert "No rule executes on its own" in str(raised.value)


def test_a_rule_cannot_ask_for_a_level_nothing_can_grant() -> None:
    with pytest.raises(ValidationError) as raised:
        Constraints(automation_level=3)

    assert "does not exist yet" in str(raised.value)


def test_a_rule_cannot_waive_the_review_s_safeguards() -> None:
    with pytest.raises(ValidationError) as raised:
        Constraints(requires_execution_confirmation=False)

    assert "not a rule's to waive" in str(raised.value)


def test_wording_may_only_say_what_the_rule_captures() -> None:
    with pytest.raises(ValidationError) as raised:
        filing_draft(
            rule_type=RuleType.EMAIL_NOTIFICATION,
            effects=[
                Effect(
                    kind=EffectKind.SHOW_NOTIFICATION,
                    message="Transfer {{reference}} lands in {{days}} days",
                )
            ],
        )

    assert "never capture" in str(raised.value)


def test_wording_that_names_a_capture_is_kept() -> None:
    draft = filing_draft(
        rule_type=RuleType.EMAIL_NOTIFICATION,
        effects=[
            Effect(
                kind=EffectKind.SHOW_NOTIFICATION,
                message="Transfer {{number}} lands in {{days}} days",
            )
        ],
    )

    assert draft.effects[0].placeholders() == {"number", "days"}
    assert draft.effects[0].effect_class() is EffectClass.DISPLAY


def test_renaming_a_rule_does_not_make_it_a_different_rule() -> None:
    assert filing_draft().digest() == filing_draft(
        name="Transfer notices", description="Better words.", priority=900
    ).digest()


def test_a_rule_of_another_type_is_not_another_version_of_this_one(session: Session) -> None:
    record = propose_rule(session, draft=filing_draft(), loaded=load(), now=NOW)

    with pytest.raises(RuleError) as raised:
        amend_rule(
            session,
            rule_id=record.rule.id,
            draft=filing_draft(
                rule_type=RuleType.EMAIL_CLASSIFICATION,
                effects=[Effect(kind=EffectKind.ASSIGN_EMAIL_GROUP, group_key="financial_taxes")],
            ),
            loaded=load(),
            now=NOW,
        )

    assert "different rule" in str(raised.value)
