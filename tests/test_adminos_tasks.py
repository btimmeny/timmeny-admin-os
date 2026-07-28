import asyncio

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from adminos.adapters.monday import MondayError, MondayItem
from adminos.db.models import (
    Classification,
    Evidence,
    ExternalMapping,
    OperationalObject,
    WorkflowRun,
    WorkflowStep,
)
from adminos.domain.classification import Disposition, Relationship, read_review_queue
from adminos.domain.duplicates import find_duplicates
from adminos.domain.tasks import (
    EvidenceNotFound,
    TaskCreationRefused,
    VerificationFailed,
    build_idempotency_key,
    create_task_from_evidence,
    decide_approval,
    verify_item,
)
from adminos.domain.workflow import MappingState, RunState


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
BOARD_ID = "8962223984"
GROUP_ID = "group_mkqmqhnc"


@pytest.fixture
def session(tmp_path: Path) -> Session:
    url = f"sqlite:///{tmp_path / 'tasks.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


def add_evidence(session: Session, subject: str = "KPMG Activities") -> Evidence:
    evidence = Evidence(
        source_system="gmail",
        source_thread_id="197b351c69d3613f",
        subject=subject,
        participants=["cpa@example.com"],
        received_at=datetime(2026, 3, 1, tzinfo=UTC),
        content_hash="hash-1",
    )
    session.add(evidence)
    session.commit()
    return evidence


def classify(session: Session, evidence: Evidence, confidence: float) -> Classification:
    classification = Classification(
        evidence_id=evidence.id,
        classifier_version="test",
        relationship_type=Relationship.CREATES,
        disposition=Disposition.CREATE_TASK if confidence else Disposition.NEEDS_REVIEW,
        confidence=confidence,
        requires_review=confidence < 1.0,
    )
    session.add(classification)
    session.commit()
    return classification


def board_item(name: str, admin_os_id: str | None = None) -> MondayItem:
    return MondayItem(
        item_id="900",
        name=name,
        group="Tasks | Action Items",
        status="Not Yet Started",
        admin_os_id=admin_os_id,
        action_date=None,
        board_id=BOARD_ID,
    )


class FakeWriter:
    """A Monday stand-in that records the writes and can be made to misbehave."""

    def __init__(
        self,
        existing: MondayItem | None = None,
        verified: MondayItem | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self.existing = existing
        self.verified = verified
        self.create_error = create_error
        self.created: list[dict[str, str | None]] = []
        self.lookups: list[str] = []
        self.reads: list[str] = []

    async def find_by_admin_os_id(self, board_id: str, admin_os_id: str) -> MondayItem | None:
        self.lookups.append(admin_os_id)
        return self.existing

    async def create_item(
        self,
        board_id: str,
        name: str,
        admin_os_id: str,
        group_id: str | None = None,
        action_date: str | None = None,
    ) -> str:
        if self.create_error is not None:
            raise self.create_error
        self.created.append(
            {
                "board_id": board_id,
                "name": name,
                "admin_os_id": admin_os_id,
                "group_id": group_id,
                "action_date": action_date,
            }
        )
        return "12345"

    async def read_item(self, item_id: str) -> MondayItem | None:
        self.reads.append(item_id)
        if self.verified is not None:
            return self.verified
        created = self.created[-1] if self.created else None
        return MondayItem(
            item_id=item_id,
            name=str(created["name"]) if created else "",
            group="Tasks | Action Items",
            status=None,
            admin_os_id=str(created["admin_os_id"]) if created else None,
            action_date=None,
            board_id=BOARD_ID,
        )


def create_task(
    session: Session,
    writer: FakeWriter,
    evidence_id: str,
    title: str = "Taxes | Confirm KPMG scope for 2026",
    items: list[MondayItem] | None = None,
    confirmed: bool = False,
    action_date: str | None = None,
):  # noqa: ANN201 - returns the domain result dataclass
    return asyncio.run(
        create_task_from_evidence(
            session,
            writer,  # type: ignore[arg-type]
            BOARD_ID,
            evidence_id,
            title,
            items or [],
            group_id=GROUP_ID,
            action_date=action_date,
            confirmed=confirmed,
        )
    )


def test_a_certain_classification_with_a_clear_board_may_create() -> None:
    decision = decide_approval(1.0, find_duplicates("Renew passport", []), confirmed=False)

    assert decision.allowed is True


def test_an_uncertain_classification_may_not_create() -> None:
    decision = decide_approval(0.0, find_duplicates("Renew passport", []), confirmed=False)

    assert decision.allowed is False
    assert "confidence" in decision.reason


def test_a_duplicate_candidate_blocks_an_otherwise_certain_task() -> None:
    """A resembling item is the board saying the work may already exist."""
    report = find_duplicates("Annual Taxes | KPMG", [board_item("Annual Taxes | KPMG")])

    decision = decide_approval(1.0, report, confirmed=False)

    assert decision.allowed is False
    assert "duplicate" in decision.reason


def test_confirmation_overrides_both_gates() -> None:
    report = find_duplicates("Annual Taxes | KPMG", [board_item("Annual Taxes | KPMG")])

    assert decide_approval(0.0, report, confirmed=True).allowed is True


def test_an_unclassified_thread_cannot_create_a_task(session: Session) -> None:
    evidence = add_evidence(session)
    writer = FakeWriter()

    with pytest.raises(TaskCreationRefused):
        create_task(session, writer, evidence.id)

    assert writer.created == []


def test_a_refusal_creates_nothing_at_all(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)

    with pytest.raises(TaskCreationRefused) as refusal:
        create_task(session, FakeWriter(), evidence.id, items=[board_item("Annual Taxes | KPMG")])

    assert session.query(WorkflowRun).count() == 0
    assert session.query(ExternalMapping).count() == 0
    assert session.query(OperationalObject).count() == 0
    assert refusal.value.report.title == "Taxes | Confirm KPMG scope for 2026"


def test_a_refusal_carries_the_matches_that_caused_it(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 1.0)

    with pytest.raises(TaskCreationRefused) as refusal:
        create_task(
            session,
            FakeWriter(),
            evidence.id,
            title="Annual Taxes | KPMG",
            items=[board_item("Annual Taxes | KPMG")],
        )

    assert refusal.value.report.matches[0].name == "Annual Taxes | KPMG"


def test_missing_evidence_is_reported(session: Session) -> None:
    with pytest.raises(EvidenceNotFound):
        create_task(session, FakeWriter(), "no-such-evidence")


def test_a_confirmed_task_is_created_and_verified(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)
    writer = FakeWriter()

    result = create_task(session, writer, evidence.id, confirmed=True)

    assert result.item_id == "12345"
    assert result.adopted is False
    assert writer.created[0]["name"] == "Taxes | Confirm KPMG scope for 2026"
    assert writer.created[0]["board_id"] == BOARD_ID
    assert writer.created[0]["group_id"] == GROUP_ID


def test_the_item_is_stamped_with_the_admin_os_id(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)
    writer = FakeWriter()

    result = create_task(session, writer, evidence.id, confirmed=True)

    assert writer.created[0]["admin_os_id"] == result.admin_os_id
    assert result.admin_os_id == result.operational_object_id


def test_an_action_date_is_passed_through(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)
    writer = FakeWriter()

    create_task(session, writer, evidence.id, confirmed=True, action_date="2026-08-15")

    assert writer.created[0]["action_date"] == "2026-08-15"


def test_the_mapping_is_reserved_before_monday_is_called(session: Session) -> None:
    """The reserved id must already be durable when the lookup happens."""
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)
    writer = FakeWriter()

    result = create_task(session, writer, evidence.id, confirmed=True)

    assert writer.lookups == [result.admin_os_id]


def test_a_verified_task_leaves_a_verified_mapping(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)

    result = create_task(session, FakeWriter(), evidence.id, confirmed=True)

    mapping = session.query(ExternalMapping).one()
    assert mapping.state == MappingState.VERIFIED
    assert mapping.external_id == "12345"
    assert mapping.board_id == BOARD_ID
    assert mapping.admin_os_id == result.admin_os_id


def test_the_run_and_its_audit_steps_are_recorded(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)

    create_task(session, FakeWriter(), evidence.id, confirmed=True)

    run = session.query(WorkflowRun).one()
    steps = session.execute(
        select(WorkflowStep).order_by(WorkflowStep.sequence)
    ).scalars().all()
    assert run.state == RunState.SUCCEEDED
    assert [step.step_name for step in steps] == ["create_item", "verify_item"]
    assert steps[-1].external_ref == "12345"


def test_an_existing_item_with_the_reserved_id_is_adopted_not_duplicated(
    session: Session,
) -> None:
    """The crash-recovery case: Monday created the item, Admin OS never heard."""
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)
    writer = FakeWriter()
    result = create_task(session, writer, evidence.id, confirmed=True)

    adopted = MondayItem(
        item_id="12345",
        name="Taxes | Confirm KPMG scope for 2026",
        group="Tasks | Action Items",
        status=None,
        admin_os_id=result.admin_os_id,
        action_date=None,
        board_id=BOARD_ID,
    )
    retry_writer = FakeWriter(existing=adopted, verified=adopted)
    retry = create_task(session, retry_writer, evidence.id, confirmed=True)

    assert retry_writer.created == []
    assert retry.adopted is True
    assert retry.item_id == result.item_id
    assert retry.admin_os_id == result.admin_os_id


def test_a_retry_reuses_the_same_run_and_mapping(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)
    first = create_task(session, FakeWriter(), evidence.id, confirmed=True)

    adopted = MondayItem(
        item_id="12345",
        name=first.title,
        group=None,
        status=None,
        admin_os_id=first.admin_os_id,
        action_date=None,
        board_id=BOARD_ID,
    )
    create_task(session, FakeWriter(existing=adopted, verified=adopted), evidence.id, confirmed=True)

    assert session.query(WorkflowRun).count() == 1
    assert session.query(ExternalMapping).count() == 1
    assert session.query(OperationalObject).count() == 1


def test_the_idempotency_key_ignores_case_and_padding() -> None:
    assert build_idempotency_key("e1", "  Annual Taxes | KPMG ") == build_idempotency_key(
        "e1", "annual taxes | kpmg"
    )


def test_a_different_title_is_a_different_task() -> None:
    assert build_idempotency_key("e1", "One") != build_idempotency_key("e1", "Two")


def test_an_item_on_another_board_fails_verification() -> None:
    item = MondayItem("1", "Task", None, None, "ao-1", None, board_id="18404353669")

    with pytest.raises(VerificationFailed):
        verify_item(item, BOARD_ID, "Task", "ao-1")


def test_an_item_without_the_reserved_id_fails_verification() -> None:
    """Whatever that item is, it is not the one this run reserved."""
    item = MondayItem("1", "Task", None, None, None, None, board_id=BOARD_ID)

    with pytest.raises(VerificationFailed):
        verify_item(item, BOARD_ID, "Task", "ao-1")


def test_a_renamed_item_fails_verification() -> None:
    item = MondayItem("1", "Something else", None, None, "ao-1", None, board_id=BOARD_ID)

    with pytest.raises(VerificationFailed):
        verify_item(item, BOARD_ID, "Task", "ao-1")


def test_a_vanished_item_fails_verification() -> None:
    with pytest.raises(VerificationFailed):
        verify_item(None, BOARD_ID, "Task", "ao-1")


def test_failed_verification_leaves_the_mapping_recoverable(session: Session) -> None:
    """The reservation survives so the item can be found rather than remade."""
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)
    wrong = MondayItem("12345", "Renamed by someone", None, None, "other", None, BOARD_ID)

    with pytest.raises(VerificationFailed):
        create_task(session, FakeWriter(verified=wrong), evidence.id, confirmed=True)

    mapping = session.query(ExternalMapping).one()
    run = session.query(WorkflowRun).one()
    assert mapping.state == MappingState.FAILED
    assert mapping.admin_os_id is not None
    assert run.state == RunState.FAILED
    assert run.last_error is not None


def test_a_failed_create_records_the_failure(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)

    with pytest.raises(MondayError):
        create_task(
            session,
            FakeWriter(create_error=MondayError("Monday is down.")),
            evidence.id,
            confirmed=True,
        )

    run = session.query(WorkflowRun).one()
    assert run.state == RunState.FAILED
    assert session.query(WorkflowStep).one().status == "failed"


def test_a_retry_after_failure_increments_the_attempt_count(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)
    with pytest.raises(MondayError):
        create_task(
            session,
            FakeWriter(create_error=MondayError("Monday is down.")),
            evidence.id,
            confirmed=True,
        )

    create_task(session, FakeWriter(), evidence.id, confirmed=True)

    run = session.query(WorkflowRun).one()
    assert run.attempt_count == 2
    assert run.state == RunState.SUCCEEDED
    assert run.last_error is None


def test_a_verified_task_takes_its_thread_out_of_the_review_queue(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)
    assert len(read_review_queue(session, 50)) == 1

    create_task(session, FakeWriter(), evidence.id, confirmed=True)

    assert read_review_queue(session, 50) == []


def test_a_failed_task_leaves_the_thread_in_the_review_queue(session: Session) -> None:
    evidence = add_evidence(session)
    classify(session, evidence, 0.0)

    with pytest.raises(MondayError):
        create_task(
            session,
            FakeWriter(create_error=MondayError("Monday is down.")),
            evidence.id,
            confirmed=True,
        )

    assert len(read_review_queue(session, 50)) == 1


def test_the_classification_is_not_rewritten_by_the_task(session: Session) -> None:
    """Classification records an inference; the workflow records the outcome."""
    evidence = add_evidence(session)
    classification = classify(session, evidence, 0.0)

    create_task(session, FakeWriter(), evidence.id, confirmed=True)

    session.refresh(classification)
    assert classification.disposition == Disposition.NEEDS_REVIEW
    assert classification.requires_review is True
