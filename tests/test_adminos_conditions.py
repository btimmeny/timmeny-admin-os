from datetime import UTC, datetime

import pytest

from pydantic import ValidationError

from adminos.domain.conditions import (
    Condition,
    ConditionGroup,
    ConditionRefused,
    Facts,
    Operator,
    check_breadth,
    email_facts,
    readable_pattern,
)


NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)

TRANSFER = "Inter Institution Transfer Request 207960765 Will Occur in 3 Days"
TRANSFER_PATTERN = (
    r"^Inter\ Institution\ Transfer\ Request\ (?P<number>\d+)\ "
    r"Will\ Occur\ in\ (?P<days>\d+)\ Days$"
)


def facts(
    subject: str = TRANSFER,
    participants: tuple[str, ...] = ("alerts@bank.example",),
    labels: tuple[str, ...] = ("INBOX",),
    snippet: str | None = "Your transfer is scheduled.",
    capability_key: str | None = "financial_taxes",
    received_at: datetime | None = datetime(2026, 7, 26, 9, 0, tzinfo=UTC),
) -> Facts:
    return email_facts(
        subject=subject,
        participants=participants,
        labels=labels,
        snippet=snippet,
        capability_key=capability_key,
        received_at=received_at,
        now=NOW,
    )


def test_a_pattern_matches_the_kind_and_captures_what_varied() -> None:
    group = ConditionGroup(
        all=[
            Condition(field="subject", operator=Operator.REGEX, value=TRANSFER_PATTERN),
            Condition(field="gmail_label", operator=Operator.CONTAINS, value="INBOX"),
        ]
    )

    match = group.test(facts())

    assert match.matched is True
    assert match.captured == {"number": "207960765", "days": "3"}
    assert match.matched_reasons() == [
        "the subject matches the pattern Inter Institution Transfer Request "
        "[number] Will Occur in [days] Days — matched "
        "(Inter Institution Transfer Request 207960765 Will Occur in 3 Days)",
        "a Gmail label on the thread contains 'INBOX' — matched (INBOX)",
    ]


def test_the_next_one_of_these_matches_with_different_numbers() -> None:
    group = ConditionGroup(
        all=[Condition(field="subject", operator=Operator.REGEX, value=TRANSFER_PATTERN)]
    )

    match = group.test(
        facts(subject="Inter Institution Transfer Request 33 Will Occur in 10 Days")
    )

    assert match.matched is True
    assert match.captured == {"number": "33", "days": "10"}


def test_a_condition_that_did_not_match_says_what_it_saw() -> None:
    group = ConditionGroup(
        all=[Condition(field="subject", operator=Operator.CONTAINS, value="Invoice")]
    )

    match = group.test(facts())

    assert match.matched is False
    assert match.reasons() == [
        "the subject contains 'Invoice' — did not match "
        "(Inter Institution Transfer Request 207960765 Will Occur in 3 Days)"
    ]


def test_a_domain_is_read_from_the_addresses_on_the_thread() -> None:
    group = ConditionGroup(
        all=[Condition(field="participant_domain", operator=Operator.EQUALS, value="bank.example")]
    )

    assert group.test(facts()).matched is True
    assert group.test(facts(participants=("someone@other.example",))).matched is False


def test_any_needs_one_of_them_and_none_needs_neither() -> None:
    group = ConditionGroup(
        any=[
            Condition(field="subject", operator=Operator.CONTAINS, value="Transfer Request"),
            Condition(field="subject", operator=Operator.CONTAINS, value="Wire Confirmation"),
        ],
        none=[Condition(field="subject", operator=Operator.CONTAINS, value="fraud alert")],
    )

    assert group.test(facts()).matched is True
    assert group.test(facts(subject="Transfer Request: fraud alert")).matched is False


def test_a_negative_condition_holds_for_every_address_not_just_one() -> None:
    group = ConditionGroup(
        all=[
            Condition(
                field="participant_domain", operator=Operator.NOT_EQUALS, value="bank.example"
            )
        ]
    )

    both = facts(participants=("alerts@bank.example", "brian@example.com"))

    assert group.test(both).matched is False


def test_an_age_is_counted_in_whole_days_from_the_newest_message() -> None:
    group = ConditionGroup(
        all=[Condition(field="thread_age_days", operator=Operator.AT_LEAST, value="2")]
    )

    assert group.test(facts()).matched is True
    assert group.test(facts(received_at=datetime(2026, 7, 27, 21, 0, tzinfo=UTC))).matched is False


def test_a_missing_field_does_not_satisfy_a_condition_about_it() -> None:
    group = ConditionGroup(
        all=[Condition(field="snippet", operator=Operator.CONTAINS, value="transfer")]
    )

    match = group.test(facts(snippet=None))

    assert match.matched is False
    assert match.reasons() == ["Gmail's preview text is not set"]


def test_there_is_no_body_field_because_there_is_no_body() -> None:
    with pytest.raises(ValidationError) as raised:
        Condition(field="body", operator=Operator.CONTAINS, value="transfer")

    assert "is not something a rule can match on" in str(raised.value)
    assert "snippet" in str(raised.value)


def test_an_operator_has_to_suit_the_field() -> None:
    with pytest.raises(ValidationError) as raised:
        Condition(field="thread_age_days", operator=Operator.CONTAINS, value="3")

    assert "does not apply to the thread's age in days" in str(raised.value)


def test_a_pattern_that_will_not_compile_is_refused_when_it_is_written() -> None:
    with pytest.raises(ValidationError) as raised:
        Condition(field="subject", operator=Operator.REGEX, value="Transfer (")

    assert "is not a usable pattern" in str(raised.value)


def test_a_group_with_nothing_in_it_is_refused() -> None:
    with pytest.raises(ValidationError) as raised:
        ConditionGroup()

    assert "matches everything" in str(raised.value)


def test_all_inbox_email_is_not_a_rule() -> None:
    group = ConditionGroup(
        all=[Condition(field="gmail_label", operator=Operator.CONTAINS, value="INBOX")]
    )

    with pytest.raises(ConditionRefused) as raised:
        check_breadth(group)

    assert "nothing in it narrows" in str(raised.value)


def test_anything_older_than_a_day_is_not_a_rule() -> None:
    group = ConditionGroup(
        all=[Condition(field="thread_age_days", operator=Operator.AT_LEAST, value="1")]
    )

    with pytest.raises(ConditionRefused):
        check_breadth(group)


def test_two_characters_of_subject_is_not_a_rule() -> None:
    group = ConditionGroup(
        all=[Condition(field="subject", operator=Operator.CONTAINS, value="an")]
    )

    with pytest.raises(ConditionRefused):
        check_breadth(group)


def test_a_pattern_matching_everything_is_not_a_rule() -> None:
    group = ConditionGroup(
        all=[Condition(field="subject", operator=Operator.REGEX, value=r"^.*$")]
    )

    with pytest.raises(ConditionRefused):
        check_breadth(group)


def test_one_broad_alternative_makes_the_whole_rule_broad() -> None:
    group = ConditionGroup(
        any=[
            Condition(field="subject", operator=Operator.CONTAINS, value="Transfer Request"),
            Condition(field="gmail_label", operator=Operator.CONTAINS, value="INBOX"),
        ]
    )

    with pytest.raises(ConditionRefused):
        check_breadth(group)


def test_a_narrowing_condition_alongside_a_broad_one_is_a_rule() -> None:
    group = ConditionGroup(
        all=[
            Condition(field="subject", operator=Operator.CONTAINS, value="Transfer Request"),
            Condition(field="gmail_label", operator=Operator.CONTAINS, value="INBOX"),
        ]
    )

    check_breadth(group)


def test_a_negation_alone_does_not_narrow() -> None:
    group = ConditionGroup(
        all=[
            Condition(
                field="subject", operator=Operator.NOT_CONTAINS, value="Transfer Request"
            )
        ]
    )

    with pytest.raises(ConditionRefused):
        check_breadth(group)


def test_a_group_reads_back_as_sentences() -> None:
    group = ConditionGroup(
        all=[Condition(field="subject", operator=Operator.REGEX, value=TRANSFER_PATTERN)],
        any=[
            Condition(field="participant_domain", operator=Operator.EQUALS, value="bank.example"),
            Condition(field="participant_domain", operator=Operator.EQUALS, value="broker.example"),
        ],
        none=[Condition(field="subject", operator=Operator.CONTAINS, value="fraud alert")],
    )

    assert group.describes() == [
        "the subject matches the pattern Inter Institution Transfer Request "
        "[number] Will Occur in [days] Days",
        "either a domain on the thread is 'bank.example' or a domain on the "
        "thread is 'broker.example'",
        "it is not the case that the subject contains 'fraud alert'",
    ]


def test_a_readable_pattern_shows_captures_as_the_values_they_stand_for() -> None:
    assert readable_pattern(TRANSFER_PATTERN) == (
        "Inter Institution Transfer Request [number] Will Occur in [days] Days"
    )


def test_a_tree_may_not_nest_beyond_reading() -> None:
    condition = Condition(field="subject", operator=Operator.CONTAINS, value="Transfer")
    nested: ConditionGroup | Condition = condition
    for _ in range(3):
        nested = ConditionGroup(all=[nested])

    with pytest.raises(ValidationError) as raised:
        ConditionGroup(all=[nested])

    assert "nobody can read back" in str(raised.value)
