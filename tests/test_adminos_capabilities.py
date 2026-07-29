import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adminos.capabilities.config import (
    DESTINATION_PARAM,
    ActionKind,
    CapabilityConfigError,
    LoadedCapabilities,
    UnknownCapability,
    clear_cache,
    load_capabilities,
    parse_capabilities,
)
from adminos.db.models import Evidence
from adminos.domain.review import evaluate_policy


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPOSITORY_ROOT / "config" / "capabilities.yaml"
NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)

MINIMAL = """
version: test.1
screens:
  - id: test-review-v1
    title: Test review
    columns:
      - {label: "#", source: index}
      - {label: What it is, source: what_it_is}
    actions:
      - {id: approve, label: Do what is recommended, decision: approve}
capabilities:
  - key: financial_taxes
    name: financial/taxes
    position: 10
    gmail:
      labels: [financial/taxes]
    presentation:
      screen: test-review-v1
    playbook:
      id: evidence_to_obligation
      steps: [collect_evidence, recommend, await_decision]
    recommendation_policy:
      version: taxes.1
      categories: [obligation]
"""


FILING = MINIMAL.replace(
    "      labels: [financial/taxes]",
    "      labels: [financial/taxes]\n      destinations: [Later]",
) + """    allowed_actions: [gmail.move]
"""
"""The same capability, allowed to file mail in one folder."""


def parse(document: str) -> LoadedCapabilities:
    return parse_capabilities(document.encode())


@pytest.fixture(autouse=True)
def clear_configuration_cache() -> None:
    clear_cache()
    yield
    clear_cache()


def test_the_shipped_configuration_is_valid() -> None:
    """The file the service actually runs on must parse, not just the fixtures."""
    loaded = load_capabilities(SHIPPED_CONFIG)

    assert [capability.key for capability in loaded.enabled()] == [
        "admin",
        "financial_taxes",
        "career_advisor_calls",
    ]


def test_the_shipped_admin_rules_only_ever_archive() -> None:
    """The first automatable capability recommends nothing irreversible."""
    admin = load_capabilities(SHIPPED_CONFIG).get("admin")

    assert admin.recommendation_policy.version == "admin.v2"
    assert {rule.recommend for rule in admin.recommendation_policy.rules} == {
        ActionKind.GMAIL_ARCHIVE
    }
    assert admin.recommendation_policy.default == "needs_review"


def test_tax_mail_may_not_be_archived_or_trashed() -> None:
    """The one capability whose mail has deadlines keeps both dispositions off."""
    taxes = load_capabilities(SHIPPED_CONFIG).get("financial_taxes")

    assert taxes.execution.permits(ActionKind.GMAIL_ARCHIVE) is False
    assert taxes.permits(ActionKind.GMAIL_TRASH) is False


def test_no_shipped_capability_disposes_of_mail_unattended() -> None:
    """Trash is reversible, but it still waits for Brian rather than a score."""
    for capability in load_capabilities(SHIPPED_CONFIG).enabled():
        assert ActionKind.GMAIL_ARCHIVE not in capability.approval.auto_approve
        assert ActionKind.GMAIL_TRASH not in capability.approval.auto_approve


def test_every_shipped_capability_can_file_mail_somewhere_named() -> None:
    """Filing is the disposition that keeps mail, so all three have folders."""
    for capability in load_capabilities(SHIPPED_CONFIG).enabled():
        assert capability.permits(ActionKind.GMAIL_MOVE)
        assert capability.execution.permits(ActionKind.GMAIL_MOVE)
        assert capability.gmail.destinations


def recommend(capability_key: str, subject: str, sender: str) -> tuple[str, str | None]:
    """What the shipped rules say about one real thread, and where it would go."""
    capability = load_capabilities(SHIPPED_CONFIG).get(capability_key)
    evidence = Evidence(
        source_system="gmail",
        source_thread_id="t1",
        subject=subject,
        participants=[sender],
        received_at=NOW - timedelta(days=1),
    )
    outcome = evaluate_policy(capability, evidence, NOW)
    return outcome.recommendation, outcome.params.get(DESTINATION_PARAM)


@pytest.mark.parametrize(
    ("subject", "sender"),
    [
        ("New Paid Survey (Warehouse)  | Arbolus", "team@arbolus.com"),
        ("Dialectica new survey project request: HR Software Space", "x@dialecticanet.com"),
        ("Survey Request: Voice AI Platforms", "research@alphasights.com"),
        ("Follow Up: True North Insights Survey", "s@truenorthinsights.com"),
    ],
)
def test_an_expert_network_survey_is_filed_with_the_surveys(subject: str, sender: str) -> None:
    """Brian keeps surveys and calls apart, so a survey names the survey folder."""
    assert recommend("career_advisor_calls", subject, sender) == (
        ActionKind.GMAIL_MOVE,
        "Career - Advisory/Expert Survey",
    )


@pytest.mark.parametrize(
    ("subject", "sender"),
    [
        ("AlphaSights Availability Request - Enterprise Software", "p@alphasights.com"),
        ("Third Bridge: New request on the IT Services Space", "r@thirdbridge.com"),
        ("Cloud Recovery Solutions Industry - Tegus Research Call", "c@tegus.com"),
    ],
)
def test_a_request_for_a_call_is_left_in_the_inbox(subject: str, sender: str) -> None:
    """A call is answered, and a thread filed out of the inbox is one nobody takes."""
    assert recommend("career_advisor_calls", subject, sender) == ("needs_review", None)


@pytest.mark.parametrize(
    ("subject", "sender"),
    [
        ("YOUR 1099 TAX INFORMATION STATEMENT IS NOW AVAILABLE", "no-reply@mail.schwab.com"),
        ("FBAR Submission Accepted FX26-00463852", "no-reply@fincen.gov"),
        ("IRS Direct Pay Confirmation of Scheduled Transaction", "no-reply@directpay.irs.gov"),
        ("KPMG México - BRIAN PATRICK TIMMENY - TAX - Edo de cuenta", "adviser@kpmg.com.mx"),
    ],
)
def test_a_tax_record_is_filed_with_the_tax_year(subject: str, sender: str) -> None:
    """A form or an acknowledgement is a record: what it needs is somewhere to live."""
    assert recommend("financial_taxes", subject, sender) == (
        ActionKind.GMAIL_MOVE,
        "Financial/Taxes",
    )


@pytest.mark.parametrize(
    ("subject", "sender"),
    [
        ("Walmart | Brian Timmeny | Missing information request", "adviser@kpmg.com.mx"),
        ("Brian Timmeny - Fee proposal 2026", "adviser@kpmg.com.mx"),
        ("RE: US Tax Briefing", "adviser@kpmg.com"),
    ],
)
def test_tax_mail_that_asks_for_something_is_left_in_the_inbox(subject: str, sender: str) -> None:
    """Filing answers nothing, and out of the inbox is how a deadline is missed."""
    assert recommend("financial_taxes", subject, sender) == ("needs_review", None)


def test_every_shipped_filing_rule_names_a_folder_the_capability_has() -> None:
    """A recommendation the move endpoint would refuse is worse than none."""
    for capability in load_capabilities(SHIPPED_CONFIG).enabled():
        for rule in capability.recommendation_policy.rules:
            if rule.recommend == ActionKind.GMAIL_MOVE:
                assert rule.move_to in capability.gmail.destinations


def test_no_shipped_destination_is_a_system_label() -> None:
    """A folder is somewhere to keep mail, not the inbox, Trash, or spam."""
    reserved = {"INBOX", "TRASH", "SPAM", "SENT", "DRAFT", "STARRED", "UNREAD", "IMPORTANT"}

    for capability in load_capabilities(SHIPPED_CONFIG).enabled():
        assert not reserved & {name.upper() for name in capability.gmail.destinations}


def test_a_gmail_system_label_cannot_be_a_folder() -> None:
    """The inbox is what a move leaves; it is not somewhere to move mail to."""
    document = MINIMAL.replace(
        "      labels: [financial/taxes]",
        "      labels: [financial/taxes]\n      destinations: [INBOX]",
    )

    with pytest.raises(CapabilityConfigError, match="is a Gmail system label"):
        parse(document)


def test_filing_without_anywhere_to_file_is_refused() -> None:
    document = MINIMAL + """    allowed_actions: [gmail.move]
"""

    with pytest.raises(CapabilityConfigError, match="without any destination"):
        parse(document)


def test_a_rule_that_files_must_say_where() -> None:
    """A recommendation to move is not a recommendation until it names a folder."""
    document = FILING.replace(
        "      categories: [obligation]",
        """      categories: [obligation]
      rules:
        - id: file_it
          when: {subject_contains: [Statement]}
          recommend: gmail.move
          rationale: Worth keeping.
""",
    )

    with pytest.raises(CapabilityConfigError, match="without saying where to"):
        parse(document)


def test_a_rule_may_not_file_mail_outside_the_capabilitys_folders() -> None:
    document = FILING.replace(
        "      categories: [obligation]",
        """      categories: [obligation]
      rules:
        - id: file_it
          when: {subject_contains: [Statement]}
          recommend: gmail.move
          move_to: Career/Citi
          rationale: Worth keeping.
""",
    )

    with pytest.raises(CapabilityConfigError, match="which is not one of"):
        parse(document)


def test_a_destination_on_an_action_that_moves_nothing_is_refused() -> None:
    document = MINIMAL.replace(
        "      categories: [obligation]",
        """      categories: [obligation]
      rules:
        - id: archive_it
          when: {subject_contains: [Statement]}
          recommend: gmail.archive
          move_to: Later
          rationale: Nothing to act on.
""",
    )

    with pytest.raises(CapabilityConfigError, match="does not move anything"):
        parse(document)


def test_being_allowed_to_approve_an_action_is_not_permission_to_run_it() -> None:
    """Two grants, so approving a Monday task cannot reach the mailbox."""
    admin = load_capabilities(SHIPPED_CONFIG).get("admin")

    assert admin.permits(ActionKind.MONDAY_CREATE_TASK) is True
    assert admin.execution.permits(ActionKind.MONDAY_CREATE_TASK) is False


def test_an_action_may_not_be_executable_without_being_allowed() -> None:
    document = MINIMAL + """    execution:
      permitted_actions: [gmail.archive]
"""

    with pytest.raises(CapabilityConfigError, match="without being allowed to approve"):
        parse(document)


def test_sending_requires_permission_to_draft() -> None:
    """Nothing may go out that was not written down and reviewed first."""
    document = MINIMAL + """    allowed_actions: [gmail.send_draft]
    execution:
      permitted_actions: [gmail.send_draft]
"""

    with pytest.raises(CapabilityConfigError, match="draft"):
        parse(document)


def test_automatable_rules_require_learning_to_be_on() -> None:
    document = MINIMAL + """    learning:
      scope: none
      allow_rule_learning: false
      allow_automatable_rules: true
"""

    with pytest.raises(CapabilityConfigError, match="without allowing rule learning"):
        parse(document)


def test_capabilities_are_ordered_by_position_not_file_order() -> None:
    document = MINIMAL + """  - key: admin
    name: Admin
    position: 5
    gmail:
      labels: [Admin]
    presentation:
      screen: test-review-v1
    playbook:
      id: triage
      steps: [collect_evidence]
    recommendation_policy:
      version: admin.1
      categories: [errand]
"""

    assert [capability.key for capability in parse(document).enabled()] == [
        "admin",
        "financial_taxes",
    ]


def test_a_disabled_capability_is_excluded() -> None:
    loaded = parse(MINIMAL.replace("position: 10", "position: 10\n    enabled: false"))

    assert loaded.enabled() == ()
    assert loaded.get("financial_taxes").enabled is False


def test_the_digest_changes_with_the_file() -> None:
    """A run records the digest, so 'which configuration produced this?' has an answer."""
    assert parse(MINIMAL).digest != parse(MINIMAL.replace("taxes.1", "taxes.2")).digest


def test_an_unknown_key_is_rejected() -> None:
    with pytest.raises(CapabilityConfigError):
        parse(MINIMAL.replace("position: 10", "position: 10\n    labell: oops"))


def test_duplicate_keys_are_rejected() -> None:
    document = MINIMAL + """  - key: financial_taxes
    name: Duplicate
    position: 20
    gmail:
      labels: [Other]
    playbook:
      id: triage
      steps: [collect_evidence]
    recommendation_policy:
      version: other.1
      categories: [errand]
"""

    with pytest.raises(CapabilityConfigError):
        parse(document)


def test_duplicate_positions_are_rejected() -> None:
    """Position orders the review, so a tie would make the order arbitrary."""
    document = MINIMAL + """  - key: admin
    name: Admin
    position: 10
    gmail:
      labels: [Admin]
    playbook:
      id: triage
      steps: [collect_evidence]
    recommendation_policy:
      version: admin.1
      categories: [errand]
"""

    with pytest.raises(CapabilityConfigError):
        parse(document)


def test_a_rule_may_not_recommend_an_action_the_capability_cannot_take() -> None:
    document = MINIMAL + """      rules:
        - id: archive_everything
          when:
            subject_contains: [Newsletter]
          recommend: gmail.archive
          rationale: Not permitted here.
"""

    with pytest.raises(CapabilityConfigError, match="not allowed"):
        parse(document)


def test_a_rule_with_no_condition_is_rejected() -> None:
    """An empty condition matches every thread, which is never what was meant."""
    document = MINIMAL + """      rules:
        - id: catch_all
          when: {}
          recommend: needs_review
          rationale: Everything.
"""

    with pytest.raises(CapabilityConfigError):
        parse(document)


def test_the_default_recommendation_may_not_be_an_action() -> None:
    """Otherwise unmatched mail is acted on by omission rather than by decision."""
    document = MINIMAL.replace(
        "      categories: [obligation]",
        "      categories: [obligation]\n      default: gmail.archive",
    )

    with pytest.raises(CapabilityConfigError, match="must not be an action"):
        parse(document)


def test_auto_approval_requires_the_action_to_be_allowed() -> None:
    document = MINIMAL + """    approval:
      auto_approve: [gmail.trash]
"""

    with pytest.raises(CapabilityConfigError, match="without being"):
        parse(document)


def test_retaining_message_content_is_refused() -> None:
    """ADR-0003 is enforced by configuration, not by convention."""
    document = MINIMAL + """    learning:
      record_message_content: true
"""

    with pytest.raises(CapabilityConfigError, match="never retained"):
        parse(document)


def test_requiring_objective_alignment_needs_a_default_objective() -> None:
    document = MINIMAL + """    objectives:
      require_alignment: true
"""

    with pytest.raises(CapabilityConfigError, match="require_alignment"):
        parse(document)


def test_an_unknown_capability_is_reported_by_name() -> None:
    with pytest.raises(UnknownCapability, match="career"):
        parse(MINIMAL).get("career")


def test_permission_is_denied_by_default() -> None:
    """A capability grants actions explicitly; nothing is permitted implicitly."""
    capability = parse(MINIMAL).get("financial_taxes")

    assert capability.permits(ActionKind.GMAIL_ARCHIVE) is False


def test_auto_approval_respects_the_confidence_floor() -> None:
    document = MINIMAL + """    allowed_actions: [gmail.archive]
    approval:
      auto_approve: [gmail.archive]
      min_confidence_for_auto: 0.9
"""
    capability = parse(document).get("financial_taxes")

    assert capability.auto_approves(ActionKind.GMAIL_ARCHIVE, 0.95) is True
    assert capability.auto_approves(ActionKind.GMAIL_ARCHIVE, 0.5) is False


def test_a_missing_file_is_reported_clearly(tmp_path: Path) -> None:
    with pytest.raises(CapabilityConfigError, match="No capability configuration"):
        load_capabilities(tmp_path / "absent.yaml")


def test_an_edited_file_is_reloaded(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.yaml"
    path.write_text(MINIMAL)
    first = load_capabilities(path)

    path.write_text(MINIMAL.replace("version: test.1", "version: test.2"))
    os.utime(path, (0, 0))

    assert load_capabilities(path).version == "test.2"
    assert first.version == "test.1"
