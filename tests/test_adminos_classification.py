from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from adminos.db.models import Classification, Evidence
from adminos.domain.classification import (
    CLASSIFIER_VERSION,
    Disposition,
    Relationship,
    classify_evidence,
    read_review_queue,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def session(tmp_path: Path) -> Session:
    url = f"sqlite:///{tmp_path / 'classification.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


def add_evidence(session: Session, thread_id: str, subject: str, day: int = 1) -> Evidence:
    evidence = Evidence(
        source_system="gmail",
        source_thread_id=thread_id,
        subject=subject,
        participants=["cpa@example.com"],
        received_at=datetime(2026, 3, day, tzinfo=UTC),
        content_hash=f"hash-{thread_id}",
    )
    session.add(evidence)
    session.commit()
    return evidence


def test_every_thread_is_routed_to_review(session: Session) -> None:
    add_evidence(session, "t1", "Q3 estimate")

    result = classify_evidence(session)
    session.commit()

    stored = session.query(Classification).one()
    assert (result.scanned, result.created, result.unchanged) == (1, 1, 0)
    assert stored.disposition == Disposition.NEEDS_REVIEW
    assert stored.requires_review is True
    assert stored.classifier_version == CLASSIFIER_VERSION


def test_classification_asserts_no_relationship_or_confidence(session: Session) -> None:
    """v1 infers nothing, so it must not record a relationship it did not derive."""
    add_evidence(session, "t1", "Q3 estimate")

    classify_evidence(session)
    session.commit()

    stored = session.query(Classification).one()
    assert stored.relationship_type == Relationship.UNDETERMINED
    assert stored.confidence == 0.0
    assert stored.matched_object_id is None
    assert stored.proposed_object_type is None


def test_reclassifying_creates_nothing_new(session: Session) -> None:
    add_evidence(session, "t1", "Q3 estimate")
    classify_evidence(session)
    session.commit()

    result = classify_evidence(session)
    session.commit()

    assert (result.created, result.unchanged) == (0, 1)
    assert session.query(Classification).count() == 1


def test_only_unclassified_evidence_is_classified(session: Session) -> None:
    add_evidence(session, "t1", "Q3 estimate")
    classify_evidence(session)
    session.commit()
    add_evidence(session, "t2", "1099 from broker", day=2)

    result = classify_evidence(session)
    session.commit()

    assert (result.scanned, result.created, result.unchanged) == (2, 1, 1)


def test_duplicate_classification_is_rejected_by_the_database(session: Session) -> None:
    """The idempotency guarantee is enforced by a constraint, not just by code."""
    evidence = add_evidence(session, "t1", "Q3 estimate")

    for _ in range(2):
        session.add(
            Classification(
                evidence_id=evidence.id,
                classifier_version=CLASSIFIER_VERSION,
                relationship_type=Relationship.UNDETERMINED,
                disposition=Disposition.NEEDS_REVIEW,
                confidence=0.0,
                requires_review=True,
            )
        )

    with pytest.raises(IntegrityError):
        session.commit()


def test_review_queue_returns_newest_first(session: Session) -> None:
    add_evidence(session, "t1", "Older", day=1)
    add_evidence(session, "t2", "Newer", day=9)
    classify_evidence(session)
    session.commit()

    items = read_review_queue(session, limit=10)

    assert [item.subject for item in items] == ["Newer", "Older"]
    assert items[0].source_thread_id == "t2"


def test_review_queue_honours_the_limit(session: Session) -> None:
    for index in range(4):
        add_evidence(session, f"t{index}", "Subject", day=index + 1)
    classify_evidence(session)
    session.commit()

    assert len(read_review_queue(session, limit=2)) == 2


def test_review_queue_is_empty_before_classification(session: Session) -> None:
    add_evidence(session, "t1", "Q3 estimate")

    assert read_review_queue(session, limit=10) == []
