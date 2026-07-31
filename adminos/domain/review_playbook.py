"""The review playbook: the phases of an administrative review, as configuration.

This is the process Admin OS hands to ChatGPT rather than the one it works
itself. ChatGPT reads the mailbox through the Gmail app; Admin OS says what
the groups are, what every reviewed thread has to say, how the result is
presented and when the phase is finished, and then holds the answer to it.

Which means every one of those things has to be data. A group defined in a
handler is a group Brian cannot change without a deployment, and a group named
only in the GPT's instructions is one Admin OS cannot check a submission
against. So the groups, their order, the required fields and the completion
criteria live in a versioned document, and a review pins the revision it was
started under.

What is *not* configuration is which phases exist and which are built. A
playbook cannot declare Monday reconciliation implemented by writing it down.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Sequence

import yaml

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from adminos.logging import get_logger


logger = get_logger(__name__)

SCHEMA_VERSION = 1
"""The layout this code reads. A document written for a later one is refused."""

DEFAULT_PLAYBOOK_ID = "brian-email-review"

EMAIL_REVIEW = "email_review"


class ReviewPlaybookError(RuntimeError):
    """Raised when a review playbook document cannot be read at all."""


class PhaseKind:
    """A phase this service knows about, and whether it can actually run it."""

    def __init__(self, key: str, label: str, implemented: bool, source: str | None) -> None:
        self.key = key
        self.label = label
        self.implemented = implemented
        self.source = source


PHASES: tuple[PhaseKind, ...] = (
    PhaseKind(EMAIL_REVIEW, "Email review", implemented=True, source="gmail"),
    PhaseKind("monday_reconciliation", "Monday reconciliation", False, "monday"),
    PhaseKind("todo_review", "To-do review", False, "monday"),
    PhaseKind("daily_plan", "Daily plan", False, None),
)


def known_phase(key: str) -> PhaseKind | None:
    for phase in PHASES:
        if phase.key == key:
            return phase
    return None


class MailboxScope(StrEnum):
    """Which mail a phase is of. Inbox only, and stated rather than assumed."""

    INBOX_ONLY = "inbox_only"


class Disposition(StrEnum):
    """What is recommended for a thread in Gmail — recommended, not done.

    Nothing in this milestone executes any of these. They are recorded as what
    the review thinks should happen, and moving mail remains a separate,
    confirmed request through the existing action path.
    """

    KEEP_IN_INBOX = "keep_in_inbox"
    ARCHIVE = "archive"
    MOVE_TO_TRASH = "move_to_trash"
    FILE_TO_EXISTING_LABEL = "file_to_existing_label"
    REPLY_REQUIRED = "reply_required"
    WAITING = "waiting"
    NONE = "none"


class Urgency(StrEnum):
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class ItemField(StrEnum):
    """Everything a reviewed thread can be asked for.

    Closed on purpose: the playbook says which of these are required, and a
    field this service cannot check for is not one it can require.
    """

    SOURCE_THREAD_ID = "source_thread_id"
    SUBJECT = "subject"
    SENDER = "sender"
    RECEIVED_AT = "received_at"
    GROUP_KEY = "group_key"
    SUMMARY = "summary"
    WHY_IT_MATTERS = "why_it_matters"
    RECOMMENDED_NEXT_ACTION = "recommended_next_action"
    RECOMMENDED_GMAIL_DISPOSITION = "recommended_gmail_disposition"
    TASK_REQUIRED = "task_required"
    URGENCY = "urgency"
    CONFIDENCE = "confidence"
    UNCERTAINTIES = "uncertainties"


IDENTITY_FIELDS = (ItemField.SOURCE_THREAD_ID, ItemField.GROUP_KEY)
"""Required whatever the playbook says: without these there is no review row."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GroupConfig(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1)
    order: int
    purpose: str = ""
    catch_all: bool = False


class SourceConfig(StrictModel):
    """Where the evidence comes from, and which part of it.

    `app: gmail` says ChatGPT reads Gmail itself in this milestone. Admin OS
    holds the interpretation, not the mail.
    """

    app: Literal["gmail"] = "gmail"
    mailbox_scope: MailboxScope = MailboxScope.INBOX_ONLY


class RenderingConfig(StrictModel):
    show_empty_groups: bool = False
    show_group_counts: bool = True
    show_recommended_order: bool = True


class SortingConfig(StrictModel):
    within_group: Literal[
        "urgency_then_received", "received_newest_first", "as_submitted"
    ] = "urgency_then_received"
    recommended_order: Literal["required", "optional"] = "required"
    """Whether one complete order across every item has to be submitted."""


class CompletionCriteria(StrictModel):
    every_source_item_classified_once: bool = True
    catch_all_required: bool = True


class PhaseConfig(StrictModel):
    """One phase of the review, configured as far as it is implemented.

    A phase that is not built carries no source and no groups, and says so.
    Leaving it out of the playbook entirely would be the worse lie: the
    process has four phases, and three of them are waiting on Admin OS.
    """

    phase_key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1)
    order: int
    source: SourceConfig | None = None
    groups: list[GroupConfig] = []
    required_item_fields: list[ItemField] = []
    rendering: RenderingConfig = RenderingConfig()
    sorting: SortingConfig = SortingConfig()
    completion_criteria: CompletionCriteria = CompletionCriteria()

    def ordered_groups(self) -> list[GroupConfig]:
        return sorted(self.groups, key=lambda group: group.order)

    def group(self, key: str) -> GroupConfig | None:
        for group in self.groups:
            if group.key == key:
                return group
        return None

    def catch_all(self) -> GroupConfig | None:
        for group in self.ordered_groups():
            if group.catch_all:
                return group
        return None

    def required_fields(self) -> tuple[ItemField, ...]:
        required = dict.fromkeys(list(IDENTITY_FIELDS) + list(self.required_item_fields))
        return tuple(required)


class ReviewPlaybookDocument(StrictModel):
    schema_version: int = SCHEMA_VERSION
    playbook_id: str = DEFAULT_PLAYBOOK_ID
    name: str
    phases: list[PhaseConfig] = Field(min_length=1)

    def ordered(self) -> list[PhaseConfig]:
        return sorted(self.phases, key=lambda phase: phase.order)

    def phase(self, phase_key: str) -> PhaseConfig | None:
        for phase in self.phases:
            if phase.phase_key == phase_key:
                return phase
        return None


class ConfigCode(StrEnum):
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNKNOWN_PHASE = "UNKNOWN_PHASE"
    DUPLICATE_PHASE = "DUPLICATE_PHASE"
    DUPLICATE_GROUP = "DUPLICATE_GROUP"
    AMBIGUOUS_ORDER = "AMBIGUOUS_ORDER"
    NO_EMAIL_PHASE = "NO_EMAIL_PHASE"
    NO_GROUPS = "NO_GROUPS"
    NO_CATCH_ALL = "NO_CATCH_ALL"
    TWO_CATCH_ALLS = "TWO_CATCH_ALLS"
    NO_SOURCE = "NO_SOURCE"
    CONFIGURED_UNAVAILABLE = "CONFIGURED_UNAVAILABLE"
    PHASE_NOT_BUILT = "PHASE_NOT_BUILT"


@dataclass(frozen=True)
class Finding:
    path: str
    code: ConfigCode
    message: str


@dataclass(frozen=True)
class ConfigReport:
    """Whether this playbook can be worked, and what is wrong where it cannot.

    An error is a playbook that never becomes active. A warning is a playbook
    that runs while saying something true about itself — that it contains a
    phase Brian works through and Admin OS cannot yet.
    """

    errors: tuple[Finding, ...] = ()
    warnings: tuple[Finding, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors


def validate_review_playbook(document: ReviewPlaybookDocument) -> ConfigReport:
    """Check the document against the phases this service actually implements."""
    errors: list[Finding] = []
    warnings: list[Finding] = []

    if document.schema_version != SCHEMA_VERSION:
        errors.append(
            Finding(
                path="schema_version",
                code=ConfigCode.UNSUPPORTED_SCHEMA,
                message=(
                    f"Review playbook schema version {document.schema_version} is not "
                    f"supported; this service reads version {SCHEMA_VERSION}."
                ),
            )
        )

    check_unique(
        [phase.phase_key for phase in document.phases],
        path="phases",
        code=ConfigCode.DUPLICATE_PHASE,
        noun="phase",
        errors=errors,
    )
    check_order([phase.order for phase in document.phases], path="phases", errors=errors)

    for phase in document.phases:
        path = f"phases.{phase.phase_key}"
        kind = known_phase(phase.phase_key)
        if kind is None:
            errors.append(
                Finding(
                    path=f"{path}.phase_key",
                    code=ConfigCode.UNKNOWN_PHASE,
                    message=(
                        f"Phase {phase.phase_key!r} is not a phase this service knows. "
                        f"Known phases: {', '.join(known.key for known in PHASES)}."
                    ),
                )
            )
            continue

        if not kind.implemented:
            if phase.groups or phase.source is not None:
                errors.append(
                    Finding(
                        path=path,
                        code=ConfigCode.CONFIGURED_UNAVAILABLE,
                        message=(
                            f"{kind.label} is configured with groups or a source and is "
                            "not implemented, so the configuration describes work that "
                            "cannot happen. Leave it named and unconfigured until it is "
                            "built."
                        ),
                    )
                )
            else:
                warnings.append(
                    Finding(
                        path=path,
                        code=ConfigCode.PHASE_NOT_BUILT,
                        message=(
                            f"{kind.label} is in the review and is not built yet, so a "
                            "review names it as unavailable and never reports it done."
                        ),
                    )
                )
            continue

        check_implemented_phase(phase, kind, path, errors)

    if document.phase(EMAIL_REVIEW) is None:
        errors.append(
            Finding(
                path="phases",
                code=ConfigCode.NO_EMAIL_PHASE,
                message=(
                    "The review playbook has no email review phase, which is the only "
                    "phase this milestone implements."
                ),
            )
        )

    return ConfigReport(errors=tuple(errors), warnings=tuple(warnings))


def check_implemented_phase(
    phase: PhaseConfig, kind: PhaseKind, path: str, errors: list[Finding]
) -> None:
    if phase.source is None:
        errors.append(
            Finding(
                path=f"{path}.source",
                code=ConfigCode.NO_SOURCE,
                message=(
                    f"{kind.label} names no source, so nothing says which mail it is "
                    "of. A review of an unstated scope is a review of a guess."
                ),
            )
        )

    if not phase.groups:
        errors.append(
            Finding(
                path=f"{path}.groups",
                code=ConfigCode.NO_GROUPS,
                message=(
                    f"{kind.label} has no groups, so every thread would have nowhere "
                    "to go."
                ),
            )
        )
        return

    check_unique(
        [group.key for group in phase.groups],
        path=f"{path}.groups",
        code=ConfigCode.DUPLICATE_GROUP,
        noun="group",
        errors=errors,
    )
    check_order([group.order for group in phase.groups], path=f"{path}.groups", errors=errors)

    catch_alls = [group for group in phase.groups if group.catch_all]
    if not catch_alls and phase.completion_criteria.catch_all_required:
        errors.append(
            Finding(
                path=f"{path}.groups",
                code=ConfigCode.NO_CATCH_ALL,
                message=(
                    f"{kind.label} has no catch-all group, so a thread that fits "
                    "nowhere would have to be forced into a group it does not belong "
                    "in, or left out of the review entirely."
                ),
            )
        )
    if len(catch_alls) > 1:
        errors.append(
            Finding(
                path=f"{path}.groups",
                code=ConfigCode.TWO_CATCH_ALLS,
                message=(
                    "Two groups are marked as the catch-all: "
                    f"{', '.join(sorted(group.key for group in catch_alls))}."
                ),
            )
        )


def check_unique(
    keys: Sequence[str],
    path: str,
    code: ConfigCode,
    noun: str,
    errors: list[Finding],
) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            errors.append(
                Finding(
                    path=path,
                    code=code,
                    message=f"The {noun} {key!r} appears twice; which one is meant?",
                )
            )
        seen.add(key)


def check_order(orders: Sequence[int], path: str, errors: list[Finding]) -> None:
    """Two things at the same position have no order, only an accident of storage."""
    if len(set(orders)) != len(orders):
        errors.append(
            Finding(
                path=path,
                code=ConfigCode.AMBIGUOUS_ORDER,
                message=(
                    "Two entries share a position, so the order they are worked in "
                    "would depend on how they happen to be stored."
                ),
            )
        )


def parse_review_playbook(raw: bytes) -> ReviewPlaybookDocument:
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ReviewPlaybookError(f"The review playbook is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise ReviewPlaybookError("The review playbook must be a mapping.")
    return read_review_playbook(document)


def read_review_playbook(document: dict[str, object]) -> ReviewPlaybookDocument:
    try:
        return ReviewPlaybookDocument.model_validate(document)
    except ValidationError as exc:
        raise ReviewPlaybookError(f"The review playbook cannot be read: {exc}") from exc


def read_review_playbook_file(path: Path) -> ReviewPlaybookDocument:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReviewPlaybookError(f"The review playbook at {path} cannot be read: {exc}") from exc
    return parse_review_playbook(raw)


def report_document(report: ConfigReport) -> dict[str, object]:
    return {
        "valid": report.valid,
        "errors": [finding_document(finding) for finding in report.errors],
        "warnings": [finding_document(finding) for finding in report.warnings],
    }


def finding_document(finding: Finding) -> dict[str, str]:
    return {"path": finding.path, "code": finding.code, "message": finding.message}


__all__ = [
    "DEFAULT_PLAYBOOK_ID",
    "EMAIL_REVIEW",
    "PHASES",
    "CompletionCriteria",
    "ConfigCode",
    "ConfigReport",
    "Disposition",
    "Finding",
    "GroupConfig",
    "ItemField",
    "MailboxScope",
    "PhaseConfig",
    "PhaseKind",
    "RenderingConfig",
    "ReviewPlaybookDocument",
    "ReviewPlaybookError",
    "SortingConfig",
    "SourceConfig",
    "Urgency",
    "known_phase",
    "parse_review_playbook",
    "read_review_playbook",
    "read_review_playbook_file",
    "report_document",
    "validate_review_playbook",
]
