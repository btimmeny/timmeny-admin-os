"""The review playbook as configuration, and what it refuses to be.

What these hold to: the shipped playbook is one this service can actually run,
a phase nobody built cannot be configured as though it were, a group that fits
nowhere always has somewhere to go, and two things cannot share a position.
"""

from typing import Any

import pytest

from adminos.domain.review_playbook import (
    EMAIL_REVIEW,
    ConfigCode,
    ItemField,
    ReviewPlaybookDocument,
    ReviewPlaybookError,
    parse_review_playbook,
    read_review_playbook,
    read_review_playbook_file,
    validate_review_playbook,
)
from adminos.domain.review_playbook_store import review_playbook_file


def document(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "playbook_id": "brian-email-review",
        "name": "A review",
        "phases": [
            {
                "phase_key": EMAIL_REVIEW,
                "label": "Email review",
                "order": 10,
                "source": {"app": "gmail", "mailbox_scope": "inbox_only"},
                "groups": [
                    {"key": "act_now", "label": "Act now", "order": 10},
                    {
                        "key": "remaining_inbox",
                        "label": "Remaining Inbox",
                        "order": 20,
                        "catch_all": True,
                    },
                ],
                "required_item_fields": ["summary"],
            }
        ],
    }
    body.update(overrides)
    return body


def codes(body: dict[str, Any]) -> list[str]:
    report = validate_review_playbook(read_review_playbook(body))
    return [error.code for error in report.errors]


def test_the_playbook_this_service_ships_with_is_one_it_can_run() -> None:
    """A seed that does not validate is a deployment that cannot start a review."""
    report = validate_review_playbook(read_review_playbook_file(review_playbook_file()))
    assert report.valid, [error.message for error in report.errors]


def test_the_shipped_playbook_requires_every_field_of_a_reviewed_thread() -> None:
    phase = read_review_playbook_file(review_playbook_file()).phase(EMAIL_REVIEW)
    assert phase is not None
    assert set(phase.required_fields()) == set(ItemField)


def test_the_shipped_playbook_names_the_phases_that_are_not_built() -> None:
    """They are part of Brian's process and none of them is implemented."""
    playbook = read_review_playbook_file(review_playbook_file())
    assert [phase.phase_key for phase in playbook.ordered()] == [
        "email_review",
        "monday_reconciliation",
        "todo_review",
        "daily_plan",
    ]
    report = validate_review_playbook(playbook)
    assert {warning.code for warning in report.warnings} == {ConfigCode.PHASE_NOT_BUILT}


def test_a_phase_this_service_does_not_know_is_refused() -> None:
    body = document()
    body["phases"].append({"phase_key": "invented", "label": "Invented", "order": 20})
    assert ConfigCode.UNKNOWN_PHASE in codes(body)


def test_a_phase_that_is_not_built_cannot_be_configured_with_work() -> None:
    """Configuring Monday reconciliation would not make it happen."""
    body = document()
    body["phases"].append(
        {
            "phase_key": "monday_reconciliation",
            "label": "Monday reconciliation",
            "order": 20,
            "source": {"app": "gmail", "mailbox_scope": "inbox_only"},
        }
    )
    assert ConfigCode.CONFIGURED_UNAVAILABLE in codes(body)


def test_a_playbook_with_no_email_phase_is_refused() -> None:
    body = document(
        phases=[{"phase_key": "todo_review", "label": "To-do review", "order": 10}]
    )
    assert ConfigCode.NO_EMAIL_PHASE in codes(body)


def test_a_phase_with_no_groups_is_refused() -> None:
    body = document()
    body["phases"][0]["groups"] = []
    assert ConfigCode.NO_GROUPS in codes(body)


def test_a_phase_with_no_catch_all_is_refused() -> None:
    body = document()
    body["phases"][0]["groups"] = [{"key": "act_now", "label": "Act now", "order": 10}]
    assert ConfigCode.NO_CATCH_ALL in codes(body)


def test_two_catch_alls_are_refused() -> None:
    body = document()
    body["phases"][0]["groups"][0]["catch_all"] = True
    assert ConfigCode.TWO_CATCH_ALLS in codes(body)


def test_two_groups_in_the_same_position_are_refused() -> None:
    """An order that depends on how rows come back is not an order."""
    body = document()
    body["phases"][0]["groups"][1]["order"] = 10
    assert ConfigCode.AMBIGUOUS_ORDER in codes(body)


def test_the_same_group_twice_is_refused() -> None:
    body = document()
    body["phases"][0]["groups"].append(
        {"key": "act_now", "label": "Act now again", "order": 30}
    )
    assert ConfigCode.DUPLICATE_GROUP in codes(body)


def test_a_phase_with_no_source_is_refused() -> None:
    body = document()
    del body["phases"][0]["source"]
    assert ConfigCode.NO_SOURCE in codes(body)


def test_a_schema_version_from_the_future_is_refused() -> None:
    assert ConfigCode.UNSUPPORTED_SCHEMA in codes(document(schema_version=2))


def test_the_identity_of_a_thread_is_required_whatever_the_playbook_says() -> None:
    body = document()
    body["phases"][0]["required_item_fields"] = []
    phase = read_review_playbook(body).phase(EMAIL_REVIEW)
    assert phase is not None
    assert phase.required_fields() == (ItemField.SOURCE_THREAD_ID, ItemField.GROUP_KEY)


def test_a_field_this_service_cannot_check_for_cannot_be_required() -> None:
    body = document()
    body["phases"][0]["required_item_fields"] = ["vibe"]
    with pytest.raises(ReviewPlaybookError):
        read_review_playbook(body)


def test_a_playbook_that_is_not_yaml_is_refused_readably() -> None:
    with pytest.raises(ReviewPlaybookError):
        parse_review_playbook(b"- not: a mapping")


def test_the_document_round_trips_through_storage() -> None:
    """It is stored as JSON and read back, so it has to survive the trip."""
    original = read_review_playbook(document())
    stored = original.model_dump(mode="json")
    assert ReviewPlaybookDocument.model_validate(stored) == original
