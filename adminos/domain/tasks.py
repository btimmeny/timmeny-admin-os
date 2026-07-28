import hashlib

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.adapters.monday import (
    EXTERNAL_SYSTEM,
    MondayError,
    MondayItem,
    MondayWriter,
)
from adminos.db.models import (
    Classification,
    Evidence,
    ExternalMapping,
    OperationalObject,
    WorkflowRun,
    WorkflowStep,
)
from adminos.domain.classification import Disposition
from adminos.domain.duplicates import DuplicateReport, find_duplicates
from adminos.domain.workflow import WORKFLOW_NAME, MappingState, RunState, StepStatus
from adminos.logging import get_logger


INTERNAL_TYPE = "operational_object"
EXTERNAL_KIND = "item"
OBJECT_TYPE = "action"
OBJECT_STATUS = "open"
OBJECT_SOURCE = "gmail"
FULL_CONFIDENCE = 1.0

logger = get_logger(__name__)


class TaskCreationRefused(RuntimeError):
    """Raised when a task needs human confirmation before it can be created."""

    def __init__(self, reason: str, report: DuplicateReport) -> None:
        super().__init__(reason)
        self.reason = reason
        self.report = report


class EvidenceNotFound(LookupError):
    """Raised when the requested evidence does not exist."""


class VerificationFailed(RuntimeError):
    """Raised when the item Monday returns is not the item we asked for."""


@dataclass(frozen=True)
class ApprovalDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class TaskCreationResult:
    run_id: str
    operational_object_id: str
    admin_os_id: str
    item_id: str
    board_id: str
    title: str
    adopted: bool
    confirmed: bool
    auto_approved: bool
    report: DuplicateReport


def decide_approval(
    confidence: float,
    report: DuplicateReport,
    confirmed: bool,
) -> ApprovalDecision:
    """Decide whether a task may be created without asking a human.

    The rule the account owner set: create unprompted only at full certainty,
    otherwise come back and ask. Certainty here is two-sided — the classifier
    must be certain the task is warranted, *and* the board must hold nothing
    that resembles it. A duplicate candidate is the board saying the work may
    already exist, which is exactly the case a human should look at.
    """
    if confirmed:
        return ApprovalDecision(True, "A human confirmed this task.")
    if confidence < FULL_CONFIDENCE:
        return ApprovalDecision(
            False,
            f"Classification confidence is {confidence}, below the {FULL_CONFIDENCE} "
            "required to create a task without confirmation.",
        )
    if report.matches:
        return ApprovalDecision(
            False,
            f"The board holds {len(report.matches)} item(s) resembling this title; "
            "confirm that it is not a duplicate.",
        )
    return ApprovalDecision(
        True,
        "Classification is certain and no board item resembles this title.",
    )


def build_idempotency_key(evidence_id: str, title: str) -> str:
    digest = hashlib.sha256(f"{evidence_id}\n{title.strip().casefold()}".encode())
    return digest.hexdigest()


async def create_task_from_evidence(
    session: Session,
    writer: MondayWriter,
    board_id: str,
    evidence_id: str,
    title: str,
    board_items: Sequence[MondayItem],
    group_id: str | None = None,
    action_date: str | None = None,
    confirmed: bool = False,
) -> TaskCreationResult:
    """Create one Monday task from one piece of evidence, and verify it landed.

    The ordering is the point. The mapping row is committed *before* Monday is
    called, so a crash between the call and the commit leaves a reserved
    `admin_os_id` that the retry finds on the board and adopts. Verification is
    a separate read of the created item, because a mutation echoing an id is
    not evidence that the item exists with the fields we asked for.
    """
    evidence = session.get(Evidence, evidence_id)
    if evidence is None:
        raise EvidenceNotFound(f"No evidence with id {evidence_id}.")

    report = find_duplicates(title, board_items)
    decision = decide_approval(read_confidence(session, evidence_id), report, confirmed)
    if not decision.allowed:
        raise TaskCreationRefused(decision.reason, report)

    key = build_idempotency_key(evidence_id, title)
    existing_run = session.execute(
        select(WorkflowRun).where(WorkflowRun.idempotency_key == key)
    ).scalar_one_or_none()
    run = existing_run or WorkflowRun(
        workflow_name=WORKFLOW_NAME,
        idempotency_key=key,
        state=RunState.RUNNING,
        evidence_id=evidence_id,
        requires_review=False,
        attempt_count=0,
    )
    run.state = RunState.RUNNING
    run.attempt_count += 1
    session.add(run)
    session.flush()

    mapping = reserve_mapping(session, run, board_id, title)
    admin_os_id = mapping.admin_os_id
    if admin_os_id is None:
        raise VerificationFailed("The reserved mapping carries no Admin OS ID.")

    # Committed before the external call: a crash after this point is
    # recoverable, a crash before it leaves nothing behind.
    session.commit()

    sequence = len(list(session.execute(
        select(WorkflowStep.id).where(WorkflowStep.run_id == run.id)
    ).scalars().all()))

    try:
        adopted_item = await writer.find_by_admin_os_id(board_id, admin_os_id)
        if adopted_item is None:
            item_id = await writer.create_item(
                board_id,
                title,
                admin_os_id,
                group_id=group_id,
                action_date=action_date,
            )
        else:
            item_id = adopted_item.item_id
        sequence += 1
        record_step(
            session,
            run,
            sequence,
            "adopt_item" if adopted_item else "create_item",
            StepStatus.SUCCEEDED,
            external_ref=item_id,
        )

        verified = await writer.read_item(item_id)
        verify_item(verified, board_id, title, admin_os_id)
    except (MondayError, VerificationFailed) as exc:
        sequence += 1
        record_step(session, run, sequence, "verify_item", StepStatus.FAILED, error=str(exc))
        mapping.state = MappingState.FAILED
        run.state = RunState.FAILED
        run.last_error = str(exc)
        session.commit()
        raise

    sequence += 1
    record_step(session, run, sequence, "verify_item", StepStatus.SUCCEEDED, external_ref=item_id)

    mapping.external_id = item_id
    mapping.state = MappingState.VERIFIED
    run.state = RunState.SUCCEEDED
    run.last_error = None
    session.commit()

    logger.info(
        "monday task verified: run=%s item=%s adopted=%s confirmed=%s",
        run.id,
        item_id,
        adopted_item is not None,
        confirmed,
    )

    return TaskCreationResult(
        run_id=run.id,
        operational_object_id=str(run.operational_object_id),
        admin_os_id=admin_os_id,
        item_id=item_id,
        board_id=board_id,
        title=title,
        adopted=adopted_item is not None,
        confirmed=confirmed,
        auto_approved=not confirmed,
        report=report,
    )


def reserve_mapping(
    session: Session,
    run: WorkflowRun,
    board_id: str,
    title: str,
) -> ExternalMapping:
    """Reserve the Admin OS identity this task will be created under.

    Re-entrant: a run that already reserved one reuses it, so a retry writes
    the same `admin_os_id` and can recognise its own half-finished work.
    """
    if run.operational_object_id is not None:
        mapping = session.execute(
            select(ExternalMapping).where(
                ExternalMapping.internal_type == INTERNAL_TYPE,
                ExternalMapping.internal_id == run.operational_object_id,
                ExternalMapping.external_system == EXTERNAL_SYSTEM,
                ExternalMapping.external_kind == EXTERNAL_KIND,
            )
        ).scalar_one_or_none()
        if mapping is not None:
            return mapping

    operational_object = OperationalObject(
        type=OBJECT_TYPE,
        title=title,
        status=OBJECT_STATUS,
        source=OBJECT_SOURCE,
    )
    session.add(operational_object)
    session.flush()

    run.operational_object_id = operational_object.id
    mapping = ExternalMapping(
        internal_type=INTERNAL_TYPE,
        internal_id=operational_object.id,
        external_system=EXTERNAL_SYSTEM,
        external_kind=EXTERNAL_KIND,
        board_id=board_id,
        state=MappingState.PENDING,
        admin_os_id=operational_object.id,
    )
    session.add(mapping)
    session.flush()
    return mapping


def verify_item(
    item: MondayItem | None,
    board_id: str,
    title: str,
    admin_os_id: str,
) -> None:
    """Confirm the item Monday returns is the one we asked it to create."""
    if item is None:
        raise VerificationFailed("Monday returned no item to verify.")
    if item.board_id is not None and item.board_id != board_id:
        raise VerificationFailed(
            f"Item {item.item_id} is on board {item.board_id}, not {board_id}."
        )
    if item.admin_os_id != admin_os_id:
        raise VerificationFailed(
            f"Item {item.item_id} carries Admin OS ID {item.admin_os_id!r}, "
            f"not {admin_os_id!r}."
        )
    if item.name.strip() != title.strip():
        raise VerificationFailed(f"Item {item.item_id} is named {item.name!r}, not {title!r}.")


def read_confidence(session: Session, evidence_id: str) -> float:
    """Return the newest classification's confidence, or zero if unclassified."""
    classification = session.execute(
        select(Classification)
        .where(Classification.evidence_id == evidence_id)
        .order_by(Classification.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if classification is None:
        return 0.0
    if classification.disposition == Disposition.RECORD_EVIDENCE_ONLY:
        return 0.0
    return classification.confidence


def record_step(
    session: Session,
    run: WorkflowRun,
    sequence: int,
    step_name: str,
    status: StepStatus,
    external_ref: str | None = None,
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    session.add(
        WorkflowStep(
            run_id=run.id,
            sequence=sequence,
            step_name=step_name,
            status=status,
            external_ref=external_ref,
            error=error,
            started_at=now,
            finished_at=now,
        )
    )


