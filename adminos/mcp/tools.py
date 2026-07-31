"""The tools, and nothing behind them but the services they call.

Each one parses its arguments, opens a transaction, calls an application
service and renders the answer. That is deliberately all: the same operations
are meant to be reachable another way later, and a rule that lives in a tool
handler is a rule the other way in does not have.

None of these reads Gmail. Admin OS holds no mailbox in this milestone — the
client reads the Inbox through the Gmail app and submits what it made of it,
and every tool here is about the process, not the mail.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from adminos.adapters.monday import MondayError, open_monday_client
from adminos.config import (
    CONFIG_BOARD_ID_VARIABLE,
    get_config_board_id,
    get_monday_token,
)
from adminos.db.engine import DatabaseNotConfigured, session_scope
from adminos.domain.configuration import ConfigurationUnavailable, read_configuration
from adminos.domain.guided_review import (
    ASSISTANT_ACTOR,
    Completion,
    EmailReviewSubmission,
    EventKind,
    PhaseStatus,
    Recorded,
    Refused,
    ReviewNotFound,
    ReviewRefused,
    ReviewView,
    counts_for,
    read_events,
    read_phase_row,
)
from adminos.domain.guided_review import complete_phase as complete_phase_service
from adminos.domain.guided_review import read_phase_playbook as read_phase_playbook_service
from adminos.domain.guided_review import read_review as read_review_service
from adminos.domain.guided_review import record_email_review as record_email_review_service
from adminos.domain.guided_review import start_review as start_review_service
from adminos.domain.playbook_store import RevisionNotFound, RevisionRefused
from adminos.domain.review_playbook import (
    EMAIL_REVIEW,
    Disposition,
    PhaseConfig,
    ReviewPlaybookError,
    Urgency,
    known_phase,
)
from adminos.logging import get_logger
from adminos.mcp.schemas import tool_input_schema


logger = get_logger(__name__)

Arguments = TypeVar("Arguments", bound=BaseModel)


class ToolError(RuntimeError):
    """A refusal the caller should read, rather than a fault in the service."""


@dataclass(frozen=True)
class ToolResult:
    """What a tool answered, and whether the answer is a refusal.

    A refusal is still an answer: it comes back as content the model can read
    and act on, marked as an error so it is never mistaken for a result.
    """

    payload: dict[str, Any]
    is_error: bool = False


@dataclass(frozen=True)
class Tool(Generic[Arguments]):
    name: str
    title: str
    description: str
    arguments: type[Arguments]
    run: Callable[[Arguments], Awaitable[ToolResult]]

    async def invoke(self, raw: dict[str, Any]) -> ToolResult:
        return await self.run(self.arguments.model_validate(raw))

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": tool_input_schema(self.arguments),
        }


class StartAdminReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fresh: bool = Field(
        default=True,
        description=(
            "Always true. Starting administrative work opens a review of the "
            "mailbox as it is now; read_admin_review reads one already open."
        ),
    )


class ReadAdminReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(min_length=1, max_length=36)


class ReadReviewPlaybookArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(min_length=1, max_length=36)
    phase_key: str = Field(default=EMAIL_REVIEW, min_length=1, max_length=255)


class CompleteReviewPhaseArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(min_length=1, max_length=36)
    phase_key: str = Field(default=EMAIL_REVIEW, min_length=1, max_length=255)


class GetAdminOsConfigurationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration_type: Literal["email"] = Field(
        default="email",
        description=(
            "Always 'email'. To-do rules and reference data are on the board and "
            "are not read here yet."
        ),
    )


async def start_admin_review(arguments: StartAdminReviewArguments) -> ToolResult:
    with session_scope() as session:
        view = start_review_service(session, fresh=arguments.fresh, actor=ASSISTANT_ACTOR)
        return ToolResult(payload=review_payload(session, view))


async def read_admin_review(arguments: ReadAdminReviewArguments) -> ToolResult:
    with session_scope() as session:
        view = read_review_service(session, arguments.review_id)
        payload = review_payload(session, view)
        payload["validation"] = validation_state(session, view)
        payload["source_snapshot"] = snapshot_payload(view)
        payload["counts_by_group"] = recorded_counts(session, view)
        return ToolResult(payload=payload)


async def read_review_playbook(arguments: ReadReviewPlaybookArguments) -> ToolResult:
    with session_scope() as session:
        view, phase = read_phase_playbook_service(
            session, arguments.review_id, arguments.phase_key
        )
        return ToolResult(
            payload={
                "review_id": view.review.id,
                "playbook_id": view.review.playbook_id,
                "playbook_version_id": view.review.playbook_revision_id,
                "phase": phase_payload(phase),
                "next_operation": view.next_operation,
            }
        )


async def record_email_review(arguments: EmailReviewSubmission) -> ToolResult:
    with session_scope() as session:
        outcome = record_email_review_service(session, arguments, actor=ASSISTANT_ACTOR)
        if isinstance(outcome, Refused):
            return ToolResult(payload=refusal_payload(outcome), is_error=True)
        return ToolResult(payload=recorded_payload(outcome))


async def complete_review_phase(arguments: CompleteReviewPhaseArguments) -> ToolResult:
    with session_scope() as session:
        completion = complete_phase_service(
            session, arguments.review_id, arguments.phase_key, actor=ASSISTANT_ACTOR
        )
        return ToolResult(payload=completion_payload(completion))


async def get_admin_os_configuration(
    arguments: GetAdminOsConfigurationArguments,
) -> ToolResult:
    """Read the configuration board. Nothing is written, kept or cached."""
    board_id = get_config_board_id()
    if board_id is None:
        raise ConfigurationUnavailable(
            f"{CONFIG_BOARD_ID_VARIABLE} is not set, so Admin OS does not know which "
            "Monday board carries the configuration. Reading a board nobody named "
            "would be answering with somebody else's rules."
        )

    token = get_monday_token()
    if token is None:
        raise ConfigurationUnavailable(
            "MONDAY_API_TOKEN is not configured, so the configuration board cannot "
            "be read. A review run without it would be run on remembered rules."
        )

    async with open_monday_client(token) as client:
        configuration = await read_configuration(
            client, board_id, arguments.configuration_type
        )
    return ToolResult(payload=configuration.payload())


TOOLS: tuple[Tool[Any], ...] = (
    Tool(
        name="start_admin_review",
        title="Start an administrative review",
        description=(
            "Open a review of the mailbox as it is now. Any review still open is "
            "set aside and kept. Returns the review, the playbook version it is "
            "held to, and the phases in order."
        ),
        arguments=StartAdminReviewArguments,
        run=start_admin_review,
    ),
    Tool(
        name="read_admin_review",
        title="Read a review",
        description=(
            "The state of a review: its pinned playbook version, its phases, what "
            "has been recorded and what Admin OS will accept next. Returns no "
            "email content."
        ),
        arguments=ReadAdminReviewArguments,
        run=read_admin_review,
    ),
    Tool(
        name="read_review_playbook",
        title="Read the playbook for a phase",
        description=(
            "The groups, their order, the fields every reviewed thread must state, "
            "how to present the result and when the phase is finished — for the "
            "playbook version this review pinned."
        ),
        arguments=ReadReviewPlaybookArguments,
        run=read_review_playbook,
    ),
    Tool(
        name="record_email_review",
        title="Record an email review",
        description=(
            "Submit every Inbox thread classified into exactly one group, with the "
            "count read and one recommended order. Refused whole if anything is "
            "missing. Dispositions are recommendations; nothing is done in Gmail."
        ),
        arguments=EmailReviewSubmission,
        run=record_email_review,
    ),
    Tool(
        name="complete_review_phase",
        title="Complete a review phase",
        description=(
            "Finish a phase whose result Admin OS has accepted. Refused when "
            "nothing valid has been recorded. Completing the email phase does not "
            "complete the review."
        ),
        arguments=CompleteReviewPhaseArguments,
        run=complete_review_phase,
    ),
    Tool(
        name="get_admin_os_configuration",
        title="Read the configuration Brian keeps in Monday",
        description=(
            "The active processes and email rules from Brian's Monday configuration "
            "board, in Order. Read at the start of a review and follow them. Reads "
            "Monday and changes nothing there."
        ),
        arguments=GetAdminOsConfigurationArguments,
        run=get_admin_os_configuration,
    ),
)

TOOL_NAMES = tuple(tool.name for tool in TOOLS)

BY_NAME = {tool.name: tool for tool in TOOLS}


def find(name: str) -> Tool[Any] | None:
    return BY_NAME.get(name)


async def call(name: str, arguments: dict[str, Any]) -> ToolResult:
    """Run one tool, turning every refusal into something the caller can read."""
    tool = find(name)
    if tool is None:
        raise ToolError(
            f"There is no tool named {name!r}. This server offers: "
            f"{', '.join(TOOL_NAMES)}."
        )

    try:
        return await tool.invoke(arguments)
    except ValidationError as exc:
        return ToolResult(
            payload={
                "status": "rejected",
                "tool": name,
                "failures": [
                    {
                        "path": ".".join(str(part) for part in error["loc"]) or name,
                        "message": error["msg"],
                    }
                    for error in exc.errors()
                ],
            },
            is_error=True,
        )
    except (
        ConfigurationUnavailable,
        DatabaseNotConfigured,
        MondayError,
        ReviewNotFound,
        ReviewRefused,
        ReviewPlaybookError,
        RevisionNotFound,
        RevisionRefused,
    ) as exc:
        logger.info("mcp tool %s refused: %s", name, exc)
        return ToolResult(
            payload={"status": "refused", "tool": name, "message": str(exc)},
            is_error=True,
        )


def review_payload(session: Session, view: ReviewView) -> dict[str, Any]:
    return {
        "review_id": view.review.id,
        "review_date": view.review.review_date.isoformat(),
        "snapshot_at": view.review.snapshot_at.isoformat(),
        "status": view.review.status,
        "playbook_id": view.review.playbook_id,
        "playbook_version_id": view.review.playbook_revision_id,
        "current_phase": view.review.current_phase_key,
        "supersedes_review_id": view.review.supersedes_review_id,
        "phase_order": [
            {
                "phase_key": phase.phase_key,
                "label": phase.label,
                "status": phase.status,
                "item_count": phase.item_count,
                "recorded_at": moment(phase.recorded_at),
                "completed_at": moment(phase.completed_at),
            }
            for phase in view.phases
        ],
        "next_operation": view.next_operation,
    }


def snapshot_payload(view: ReviewView) -> dict[str, Any] | None:
    snapshot = view.snapshot
    if snapshot is None:
        return None
    return {
        "source": snapshot.source,
        "mailbox_scope": snapshot.mailbox_scope,
        "observed_at": moment(snapshot.observed_at),
        "thread_count": snapshot.thread_count,
        "item_count": snapshot.item_count,
        "recorded_at": moment(snapshot.recorded_at),
    }


def recorded_counts(session: Session, view: ReviewView) -> dict[str, int]:
    if view.review.current_phase_key is None:
        return {}
    phase = read_phase_row(session, view.review.id, view.review.current_phase_key)
    return counts_for(session, phase.id)


def validation_state(session: Session, view: ReviewView) -> dict[str, Any]:
    """Whether what was last submitted was accepted, and what was wrong if not."""
    for event in reversed(read_events(session, view.review.id)):
        if event.kind == EventKind.EMAIL_REVIEW_RECORDED:
            detail = dict(event.detail or {})
            return {
                "state": "recorded",
                "at": moment(event.created_at),
                "warnings": detail.get("warnings", []),
            }
        if event.kind == EventKind.VALIDATION_FAILED:
            detail = dict(event.detail or {})
            return {
                "state": "rejected",
                "at": moment(event.created_at),
                "failures": detail.get("failures", []),
            }
    return {"state": "nothing_submitted"}


def phase_payload(phase: PhaseConfig) -> dict[str, Any]:
    kind = known_phase(phase.phase_key)
    available = kind is not None and kind.implemented
    payload: dict[str, Any] = {
        "phase_key": phase.phase_key,
        "label": phase.label,
        "order": phase.order,
        "status": PhaseStatus.READY if available else PhaseStatus.UNAVAILABLE,
    }
    if not available:
        payload["message"] = (
            f"{phase.label} is not implemented yet. It is part of the process and "
            "must never be reported as done."
        )
        return payload

    source = phase.source
    payload["source"] = (
        {"app": source.app, "mailbox_scope": source.mailbox_scope}
        if source is not None
        else None
    )
    payload["groups"] = [
        {
            "key": group.key,
            "label": group.label,
            "order": group.order,
            "purpose": group.purpose,
            "catch_all": group.catch_all,
        }
        for group in phase.ordered_groups()
    ]
    payload["required_item_fields"] = [field.value for field in phase.required_fields()]
    payload["allowed_values"] = {
        "recommended_gmail_disposition": [value.value for value in Disposition],
        "urgency": [value.value for value in Urgency],
    }
    payload["rendering"] = phase.rendering.model_dump(mode="json")
    payload["sorting"] = phase.sorting.model_dump(mode="json")
    payload["completion_criteria"] = phase.completion_criteria.model_dump(mode="json")
    payload["execution"] = {
        "dispositions_are_recommendations": True,
        "message": (
            "Recording a disposition changes nothing in Gmail. Executing one is a "
            "separate request Brian confirms."
        ),
    }
    return payload


def recorded_payload(recorded: Recorded) -> dict[str, Any]:
    return {
        "review_id": recorded.review_id,
        "phase_key": recorded.phase_key,
        "status": "recorded",
        "snapshot_id": recorded.snapshot_id,
        "item_count": recorded.item_count,
        "counts_by_group": recorded.counts_by_group,
        "validation_warnings": list(recorded.warnings),
        "executed": False,
        "next_operation": "complete_review_phase",
    }


def refusal_payload(refused: Refused) -> dict[str, Any]:
    return {
        "review_id": refused.review_id,
        "phase_key": refused.phase_key,
        "status": "rejected",
        "recorded": False,
        "failures": [
            {"code": failure.code, "path": failure.path, "message": failure.message}
            for failure in refused.failures
        ],
        "next_operation": "record_email_review",
    }


def completion_payload(completion: Completion) -> dict[str, Any]:
    return {
        "review_id": completion.review_id,
        "completed_phase": completion.completed_phase,
        "next_phase": completion.next_phase,
        "next_phase_status": completion.next_phase_status,
        "review_status": completion.review_status,
        "message": completion.message,
    }


def moment(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = ["TOOLS", "TOOL_NAMES", "Tool", "ToolError", "ToolResult", "call", "find"]
