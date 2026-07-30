from datetime import UTC, datetime

import pytest

from adminos.domain.conditions import ConditionGroup, Facts, Operator, email_facts
from adminos.domain.patterns import Suggestion, suggest_subject_conditions


NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)

TRANSFER = "Inter Institution Transfer Request 207960765 Will Occur in 3 Days"
ANOTHER_TRANSFER = "Inter Institution Transfer Request 44112 Will Occur in 5 Days"


def facts(subject: str) -> Facts:
    return email_facts(
        subject=subject,
        participants=("alerts@bank.example",),
        labels=("INBOX",),
        snippet=None,
        capability_key="financial_taxes",
        received_at=datetime(2026, 7, 27, 9, 0, tzinfo=UTC),
        now=NOW,
    )


def suggestion(subject: str, key: str) -> Suggestion:
    for found in suggest_subject_conditions(subject):
        if found.key == key:
            return found
    raise AssertionError(f"no {key} suggestion for {subject!r}")


def test_the_numbers_are_the_part_that_varies() -> None:
    found = suggestion(TRANSFER, "pattern")

    assert found.condition.operator is Operator.REGEX
    assert found.summary == (
        "The subject reads Inter Institution Transfer Request [number_1] "
        "Will Occur in [number_2] Days"
    )


def test_the_pattern_catches_the_next_one_and_not_a_different_notice() -> None:
    group = ConditionGroup(all=[suggestion(TRANSFER, "pattern").condition])

    assert group.test(facts(TRANSFER)).matched is True
    assert group.test(facts(ANOTHER_TRANSFER)).matched is True
    assert group.test(facts("Wire Confirmation 44112 Will Occur in 5 Days")).matched is False


def test_the_captured_numbers_come_back_named() -> None:
    group = ConditionGroup(all=[suggestion(TRANSFER, "pattern").condition])

    assert group.test(facts(ANOTHER_TRANSFER)).captured == {
        "number_1": "44112",
        "number_2": "5",
    }


def test_one_number_is_captured_without_a_suffix() -> None:
    group = ConditionGroup(all=[suggestion("Invoice 8891 is ready", "pattern").condition])

    assert group.test(facts("Invoice 22 is ready")).captured == {"number": "22"}


def test_the_exact_subject_catches_this_one_and_misses_the_next() -> None:
    group = ConditionGroup(all=[suggestion(TRANSFER, "exact").condition])

    assert group.test(facts(TRANSFER)).matched is True
    assert group.test(facts(ANOTHER_TRANSFER)).matched is False


def test_the_readings_are_offered_narrowest_first() -> None:
    found = suggest_subject_conditions(TRANSFER)

    assert [suggestion.key for suggestion in found] == [
        "exact",
        "pattern",
        "starts_with",
        "contains",
    ]
    assert [suggestion.breadth for suggestion in found] == sorted(
        suggestion.breadth for suggestion in found
    )


def test_each_reading_says_what_it_would_catch_and_what_it_would_miss() -> None:
    for found in suggest_subject_conditions(TRANSFER):
        assert found.catches
        assert found.misses
        assert found.label


def test_the_opening_words_stop_at_the_first_number() -> None:
    assert suggestion(TRANSFER, "starts_with").condition.value == (
        "Inter Institution Transfer Request"
    )


def test_a_subject_with_no_numbers_is_offered_no_pattern() -> None:
    found = suggest_subject_conditions("Quarterly statement available")

    assert [suggestion.key for suggestion in found] == ["exact", "contains"]


def test_a_phrase_too_short_to_be_a_rule_is_not_offered() -> None:
    found = suggest_subject_conditions("A 1 B 2")

    assert [suggestion.key for suggestion in found] == ["exact"]


def test_punctuation_in_the_subject_does_not_break_the_pattern() -> None:
    subject = "Payment (ref. 8891) — action needed?"
    group = ConditionGroup(all=[suggestion(subject, "pattern").condition])

    assert group.test(facts(subject)).matched is True
    assert group.test(facts("Payment (ref. 12) — action needed?")).matched is True
    assert group.test(facts("Payment ref 12 action needed")).matched is False


def test_an_empty_subject_is_nothing_to_learn_from() -> None:
    with pytest.raises(ValueError):
        suggest_subject_conditions("   ")
