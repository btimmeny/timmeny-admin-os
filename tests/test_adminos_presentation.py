from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from adminos.capabilities.config import (
    CapabilityConfigError,
    load_capabilities,
    parse_capabilities,
)
from adminos.capabilities.screens import (
    FORMATS_BY_TYPE,
    SOURCE_TYPES,
    ColumnSource,
    ScreenConfig,
)
from adminos.db.models import ReviewGroup, ReviewItem, ReviewRun
from adminos.domain.presentation import (
    DEFAULT_ACTION_LABELS,
    read_source,
    relative_age,
    render_group,
)
from adminos.domain.review import GroupView
from conftest import build_capability, build_screen


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPOSITORY_ROOT / "config" / "capabilities.yaml"

ADMIN_REVIEW_COLUMNS = [
    "#",
    "Group",
    "What it is",
    "Key Facts",
    "Recommended Action",
    "Why",
    "Confidence",
    "Decision",
]

NOW = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)


def build_run() -> ReviewRun:
    return ReviewRun(
        id="run-1",
        review_date=date(2026, 7, 30),
        channel="email",
        state="in_progress",
        config_version="test.1",
        config_digest="digest",
    )


def build_item(
    item_id: str = "item-1",
    subject: str | None = "Daily Digest",
    recommendation: str = "gmail.archive",
    confidence: float = 0.95,
    state: str = "pending",
    received_at: datetime | None = datetime(2026, 7, 27, tzinfo=UTC),
    **overrides: object,
) -> ReviewItem:
    item = ReviewItem(
        id=item_id,
        run_id="run-1",
        group_id="group-1",
        evidence_id=f"evidence-{item_id}",
        source_thread_id=f"thread-{item_id}",
        subject=subject,
        participants=["usps@email.informeddelivery.usps.com"],
        received_at=received_at,
        state=state,
        recommendation=recommendation,
        recommendation_source="policy",
        recommendation_confidence=confidence,
        recommendation_rationale="A digest has nothing to act on once seen.",
        policy_version="admin.v2",
    )
    for name, value in overrides.items():
        setattr(item, name, value)
    return item


def build_view(*items: ReviewItem, **capability_overrides: object) -> GroupView:
    capability = build_capability(key="admin", **capability_overrides)
    group = ReviewGroup(
        id="group-1",
        run_id="run-1",
        capability_key="admin",
        capability_name=capability.name,
        position=10,
        state="in_progress",
        policy_version="admin.v2",
    )
    return GroupView(group=group, capability=capability, items=list(items))


def test_the_shipped_admin_screen_has_the_agreed_columns_in_order() -> None:
    """The contract Brian asked for, in his order. Changing it needs a new version."""
    loaded = load_capabilities(SHIPPED_CONFIG)
    screen = loaded.screen_for(loaded.get("admin"))

    assert screen.id == "admin-review-v1"
    assert [column.label for column in screen.columns] == ADMIN_REVIEW_COLUMNS


def test_every_shipped_capability_owns_a_screen() -> None:
    loaded = load_capabilities(SHIPPED_CONFIG)

    assert [loaded.screen_for(capability).id for capability in loaded.enabled()] == [
        "admin-review-v1",
        "tax-review-v1",
        "advisor-review-v1",
    ]


def test_a_row_is_finished_text_in_column_order() -> None:
    """The renderer prints cells; it does not format, order, or word anything."""
    view = build_view(build_item())

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert [column.label for column in screen.columns] == ADMIN_REVIEW_COLUMNS
    assert screen.rows[0].cells == [
        "1",
        "admin",
        "Daily Digest",
        "usps@email.informeddelivery.usps.com · 3 days ago",
        "Archive it",
        "A digest has nothing to act on once seen.",
        "95%",
        "Pending",
    ]


def test_an_unconfident_recommendation_shows_no_percentage() -> None:
    """Zero confidence is an absence, and reads as one rather than as 0%."""
    view = build_view(build_item(recommendation="needs_review", confidence=0.0))

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert screen.rows[0].cells[4] == "Needs review"
    assert screen.rows[0].cells[6] == "—"


def test_a_decided_item_shows_what_was_decided_and_that_it_has_not_happened() -> None:
    view = build_view(build_item(state="approved", approved_action="gmail.archive"))

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert screen.rows[0].cells[7] == "Archive it — decided, not yet done"


def test_a_recommended_move_names_the_folder_in_the_cell() -> None:
    """"File it" answers nothing; "File it in Later" is a recommendation."""
    view = build_view(
        build_item(recommendation="gmail.move", recommendation_params={"label": "Later"}),
        gmail={"labels": ["Admin"], "destinations": ["Later"]},
        allowed_actions=["gmail.label", "gmail.archive", "gmail.move"],
        execution={"permitted_actions": ["gmail.label", "gmail.archive", "gmail.move"]},
    )

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert screen.rows[0].cells[4] == "File it in Later"


def test_a_decided_move_says_where_it_was_filed() -> None:
    view = build_view(
        build_item(
            state="approved",
            approved_action="gmail.move",
            approved_params={"label": "Later"},
        ),
        gmail={"labels": ["Admin"], "destinations": ["Later"]},
        allowed_actions=["gmail.label", "gmail.archive", "gmail.move"],
        execution={"permitted_actions": ["gmail.label", "gmail.archive", "gmail.move"]},
    )

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert screen.rows[0].cells[7] == "File it in Later — decided, not yet done"


def test_a_move_action_carries_the_folders_it_will_accept() -> None:
    """The renderer offers a choice from the mailbox, and cannot type its own."""
    view = build_view(
        build_item(),
        gmail={"labels": ["Admin"], "destinations": ["Later", "Notes"]},
        allowed_actions=["gmail.label", "gmail.archive", "gmail.move"],
        execution={"permitted_actions": ["gmail.label", "gmail.archive", "gmail.move"]},
    )
    screen = build_screen(
        actions=[
            {
                "id": "move_gmail_thread_to_label",
                "label": "File it in a folder",
                "decision": "override",
                "action": "gmail.move",
            }
        ]
    )

    rendered = render_group(screen, view, build_run(), now=NOW)
    folder = rendered.actions[0].params[0]

    assert folder.name == "label"
    assert folder.required is True
    assert folder.choices == ["Later", "Notes"]


def test_an_action_that_needs_nothing_said_carries_no_parameters() -> None:
    view = build_view(build_item())

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert all(action.params == [] for action in screen.actions)


def test_a_category_says_what_the_thread_is() -> None:
    view = build_view(build_item(category="filing_obligation", subject="2025 return"))

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert screen.rows[0].cells[2] == "Filing obligation: 2025 return"


def test_a_long_value_is_truncated_by_the_contract() -> None:
    view = build_view(build_item(subject="x" * 200))
    screen = build_screen(
        columns=[{"label": "What it is", "source": "what_it_is", "max_chars": 20}],
    )

    rendered = render_group(screen, view, build_run(), now=NOW)

    assert rendered.rows[0].cells == ["x" * 19 + "…"]


def test_rows_are_ordered_by_the_contract_with_undated_threads_last() -> None:
    view = build_view(
        build_item("older", received_at=datetime(2026, 7, 1, tzinfo=UTC)),
        build_item("undated", received_at=None),
        build_item("newer", received_at=datetime(2026, 7, 29, tzinfo=UTC)),
    )

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert [row.item_id for row in screen.rows] == ["newer", "older", "undated"]


def test_the_index_column_counts_the_sorted_rows() -> None:
    view = build_view(
        build_item("older", received_at=datetime(2026, 7, 1, tzinfo=UTC)),
        build_item("newer", received_at=datetime(2026, 7, 29, tzinfo=UTC)),
    )

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert [row.cells[0] for row in screen.rows] == ["1", "2"]
    assert [row.item_id for row in screen.rows] == ["newer", "older"]


def test_an_action_carries_the_request_that_sends_it() -> None:
    """A renderer needs no knowledge of the API: the call is in the contract."""
    view = build_view(build_item())

    screen = render_group(build_screen(), view, build_run(), now=NOW)
    archive = next(action for action in screen.actions if action.id == "archive")

    assert archive.method == "POST"
    assert archive.path == "/review/runs/run-1/items/{item_id}/decision"
    assert archive.body == {"decision": "override", "action": "gmail.archive"}


def test_a_group_action_names_the_group_it_applies_to() -> None:
    view = build_view(build_item())
    screen = build_screen(
        actions=[
            {
                "id": "archive_all",
                "label": "Archive all of these",
                "decision": "override",
                "action": "gmail.archive",
                "scope": "group",
            }
        ]
    )

    rendered = render_group(screen, view, build_run(), now=NOW)

    assert rendered.actions[0].path == "/review/runs/run-1/groups/admin/decisions"


def test_an_action_the_capability_cannot_take_is_not_offered() -> None:
    """A shared screen never advertises a button this capability would refuse."""
    view = build_view(
        build_item(),
        allowed_actions=["gmail.label"],
        execution={"permitted_actions": ["gmail.label"]},
    )

    screen = render_group(build_screen(), view, build_run(), now=NOW)

    assert [action.id for action in screen.actions] == ["approve", "dismiss", "defer"]


def test_a_row_only_offers_what_that_row_would_accept() -> None:
    """Approval is meaningless without a recommendation, so it is not offered."""
    view = build_view(
        build_item("actionable", recommendation="gmail.archive"),
        build_item("unclear", recommendation="needs_review", confidence=0.0),
        build_item("done", state="dismissed"),
    )

    screen = render_group(build_screen(), view, build_run(), now=NOW)
    offered = {row.item_id: row.actions for row in screen.rows}

    assert offered["actionable"] == ["approve", "archive", "dismiss", "defer"]
    assert offered["unclear"] == ["archive", "dismiss", "defer"]
    assert offered["done"] == []


DISPOSAL_ACTIONS = [
    {
        "id": "archive_gmail_thread",
        "label": "Archive",
        "decision": "override",
        "action": "gmail.archive",
    },
    {
        "id": "move_gmail_thread_to_trash",
        "label": "Move to Trash",
        "decision": "override",
        "action": "gmail.trash",
    },
]


def test_an_eligible_gmail_row_offers_both_dispositions_by_their_canonical_names() -> None:
    """The row says what may be done to it, so nothing downstream has to guess."""
    view = build_view(
        build_item(),
        allowed_actions=["gmail.archive", "gmail.trash"],
        execution={"permitted_actions": ["gmail.archive", "gmail.trash"]},
    )

    rendered = render_group(build_screen(actions=DISPOSAL_ACTIONS), view, build_run(), now=NOW)

    assert rendered.rows[0].actions == ["archive_gmail_thread", "move_gmail_thread_to_trash"]


def test_a_capability_that_may_not_trash_never_offers_it() -> None:
    """Absence is the permission answer: tax mail simply has no Trash button."""
    view = build_view(
        build_item(),
        allowed_actions=["gmail.archive"],
        execution={"permitted_actions": ["gmail.archive"]},
    )

    rendered = render_group(build_screen(actions=DISPOSAL_ACTIONS), view, build_run(), now=NOW)

    assert rendered.rows[0].actions == ["archive_gmail_thread"]
    assert [action.id for action in rendered.actions] == ["archive_gmail_thread"]


def test_a_settled_row_offers_nothing() -> None:
    view = build_view(
        build_item("done", state="executed"),
        allowed_actions=["gmail.archive", "gmail.trash"],
        execution={"permitted_actions": ["gmail.archive", "gmail.trash"]},
    )

    rendered = render_group(build_screen(actions=DISPOSAL_ACTIONS), view, build_run(), now=NOW)

    assert rendered.rows[0].actions == []


def test_a_screen_may_leave_out_the_threads_that_are_finished_with() -> None:
    """An archived thread is done: showing it again invites deciding it twice."""
    view = build_view(
        build_item("waiting"),
        build_item("archived", state="executed"),
        build_item("dropped", state="dismissed"),
    )

    rendered = render_group(
        build_screen(rows="unresolved", footer="{remaining_items} still need you."),
        view,
        build_run(),
        now=NOW,
    )

    assert [row.item_id for row in rendered.rows] == ["waiting"]
    assert rendered.footer == "1 item still need you."


def test_the_shipped_screens_show_only_what_is_still_open() -> None:
    loaded = load_capabilities(SHIPPED_CONFIG)

    for capability in loaded.enabled():
        assert loaded.screen_for(capability).rows == "unresolved"


def test_the_shipped_admin_screen_offers_both_dispositions() -> None:
    loaded = load_capabilities(SHIPPED_CONFIG)
    screen = loaded.screen_for(loaded.get("admin"))

    offered = {action.id: action.action for action in screen.actions}

    assert offered["archive_gmail_thread"] == "gmail.archive"
    assert offered["move_gmail_thread_to_trash"] == "gmail.trash"
    assert offered["move_gmail_thread_to_trash_all"] == "gmail.trash"


def test_the_footer_is_filled_in_by_the_service() -> None:
    view = build_view(build_item("a"), build_item("b", state="dismissed"))
    screen = build_screen(rows="unresolved", footer="{remaining} in {capability} still need you.")

    rendered = render_group(screen, view, build_run(), now=NOW)

    assert rendered.footer == "1 in admin still need you."


def test_the_footer_counts_the_rows_it_is_under() -> None:
    """The bug: four rows described as "4 of 28", which is this morning's number."""
    settled = [build_item(f"done-{index}", state="executed") for index in range(24)]
    view = build_view(*settled, *[build_item(f"open-{index}") for index in range(4)])
    screen = build_screen(rows="unresolved", footer="{remaining_items} still need you.")

    rendered = render_group(screen, view, build_run(), now=NOW)

    assert len(rendered.rows) == 4
    assert rendered.footer == "4 items still need you."
    assert "28" not in rendered.footer


def test_one_row_left_is_one_item_not_one_items() -> None:
    view = build_view(build_item("open"), build_item("done", state="executed"))
    screen = build_screen(rows="unresolved", footer="{remaining_items} still need you.")

    assert render_group(screen, view, build_run(), now=NOW).footer == "1 item still need you."


def test_a_footer_cannot_count_the_items_that_are_no_longer_shown() -> None:
    """There is no substitution for the group's original size, deliberately."""
    with pytest.raises(ValidationError, match="not a known value"):
        build_screen(footer="{pending} of {total} still need you.")


def test_an_unknown_footer_value_is_refused() -> None:
    with pytest.raises(ValidationError, match="not a known value"):
        build_screen(footer="{invented} left")


def test_a_column_may_not_be_formatted_in_a_way_its_value_cannot_be() -> None:
    with pytest.raises(ValidationError, match="it can only be"):
        build_screen(columns=[{"label": "What it is", "source": "what_it_is", "format": "percent"}])


def test_rows_may_not_be_ordered_by_a_value_with_no_order() -> None:
    with pytest.raises(ValidationError, match="cannot be ordered by"):
        build_screen(sort=[{"source": "key_facts"}])


def test_a_screen_may_not_show_the_same_value_twice() -> None:
    with pytest.raises(ValidationError, match="same value in two columns"):
        build_screen(
            columns=[
                {"label": "What it is", "source": "what_it_is"},
                {"label": "Subject", "source": "what_it_is"},
            ]
        )


def test_an_override_must_name_its_action() -> None:
    with pytest.raises(ValidationError, match="overrides without naming an action"):
        build_screen(actions=[{"id": "x", "label": "Do it", "decision": "override"}])


def test_an_approval_may_not_name_an_action() -> None:
    with pytest.raises(ValidationError, match="only an override chooses an action"):
        build_screen(
            actions=[
                {
                    "id": "x",
                    "label": "Do it",
                    "decision": "approve",
                    "action": "gmail.archive",
                }
            ]
        )


def test_a_screen_id_must_be_versioned() -> None:
    with pytest.raises(ValidationError):
        build_screen("admin-review")


def test_every_source_can_be_read_and_formatted() -> None:
    """A column can never name a value the service does not produce."""
    capability = build_capability(key="admin")
    item = build_item()

    for source in ColumnSource:
        assert source in SOURCE_TYPES
        assert FORMATS_BY_TYPE[SOURCE_TYPES[source]]
        read_source(source, 1, item, capability, DEFAULT_ACTION_LABELS, NOW)


@pytest.mark.parametrize(
    ("received_at", "expected"),
    [
        (datetime(2026, 7, 30, 8, tzinfo=UTC), "today"),
        (datetime(2026, 7, 29, 8, tzinfo=UTC), "yesterday"),
        (datetime(2026, 7, 25, 8, tzinfo=UTC), "5 days ago"),
        (datetime(2026, 5, 30, 8, tzinfo=UTC), "2 months ago"),
        (datetime(2024, 7, 30, 8, tzinfo=UTC), "2 years ago"),
    ],
)
def test_an_age_reads_as_a_person_would_say_it(received_at: datetime, expected: str) -> None:
    assert relative_age(received_at, NOW) == expected


def test_a_capability_may_not_reference_a_screen_that_does_not_exist() -> None:
    document = SHIPPED_CONFIG.read_bytes().replace(b"screen: tax-review-v1", b"screen: nope-v1")

    with pytest.raises(CapabilityConfigError, match="which is not defined"):
        parse_capabilities(document)


def test_a_screen_may_not_offer_an_action_the_capability_is_not_allowed() -> None:
    """Financial/Taxes may not send a draft, so its screen may not offer sending."""
    tax_footer = "    footer: >-\n      {remaining_items} still need you. Nothing here is archived"
    send_action = (
        "      - id: send\n"
        "        label: Send it\n"
        "        decision: override\n"
        "        action: gmail.send_draft\n"
    )
    original = SHIPPED_CONFIG.read_text()
    assert original.count(tax_footer) == 1
    document = original.replace(tax_footer, send_action + tax_footer)

    with pytest.raises(CapabilityConfigError, match="which is not allowed to do it"):
        parse_capabilities(document.encode())


def test_a_screen_may_not_offer_bulk_decisions_where_they_are_not_taken() -> None:
    document = SHIPPED_CONFIG.read_text().replace(
        "allow_bulk_decisions: true",
        "allow_bulk_decisions: false",
        1,
    )

    with pytest.raises(CapabilityConfigError, match="does not take bulk decisions"):
        parse_capabilities(document.encode())


def test_a_screen_may_not_label_something_that_is_not_an_outcome() -> None:
    screen = build_screen(action_labels={"gmail.teleport": "Teleport it"})

    assert isinstance(screen, ScreenConfig)  # valid alone; refused in a set

    document = SHIPPED_CONFIG.read_text().replace(
        """  - id: admin-review-v1
    kind: table""",
        """  - id: admin-review-v1
    kind: table
    action_labels:
      gmail.teleport: Teleport it""",
    )

    with pytest.raises(CapabilityConfigError, match="not a recommendable outcome"):
        parse_capabilities(document.encode())


def test_a_bulk_decision_is_not_offered_where_bulk_decisions_are_refused() -> None:
    view = build_view(build_item(), approval={"allow_bulk_decisions": False})
    screen = build_screen(
        actions=[
            {"id": "approve", "label": "Do what is recommended", "decision": "approve"},
            {
                "id": "archive_all",
                "label": "Archive all of these",
                "decision": "override",
                "action": "gmail.archive",
                "scope": "group",
            },
        ]
    )

    rendered = render_group(screen, view, build_run(), now=NOW)

    assert [action.id for action in rendered.actions] == ["approve"]
