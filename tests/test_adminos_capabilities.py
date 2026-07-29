import os
from pathlib import Path

import pytest

from adminos.capabilities.config import (
    ActionKind,
    CapabilityConfigError,
    LoadedCapabilities,
    UnknownCapability,
    clear_cache,
    load_capabilities,
    parse_capabilities,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPOSITORY_ROOT / "config" / "capabilities.yaml"

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
