from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from adminos.db.models import Classification, Evidence
from adminos.logging import get_logger


CLASSIFIER_VERSION = "v1-review-all"
NO_CONFIDENCE = 0.0
RATIONALE = (
    "Classifier v1 asserts nothing about intake threads and routes every one to "
    "human review."
)

logger = get_logger(__name__)


class Relationship(StrEnum):
    """How evidence relates to an operational object."""

    CREATES = "creates"
    UPDATES = "updates"
    COMPLETES = "completes"
    BLOCKS = "blocks"
    CONTRADICTS = "contradicts"
    SUPPORTS = "supports"
    UNDETERMINED = "undetermined"


class Disposition(StrEnum):
    """What the workflow should do with a piece of evidence."""

    NEEDS_REVIEW = "needs_review"
    RECORD_EVIDENCE_ONLY = "record_evidence_only"
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"


@dataclass(frozen=True)
class ClassificationRunResult:
    classifier_version: str
    scanned: int
    created: int
    unchanged: int


@dataclass(frozen=True)
class ReviewItem:
    classification_id: str
    evidence_id: str
    source_thread_id: str
    subject: str | None
    received_at: datetime | None
    disposition: str
    rationale: str | None


def classify_evidence(session: Session) -> ClassificationRunResult:
    """Classify every unclassified piece of evidence.

    Version 1 makes no inference: it marks each thread `needs_review` with zero
    confidence and an `undetermined` relationship. That is deliberate — the
    alternative is guessing at whether a tax email is an obligation, and a wrong
    guess here becomes a Monday task or a missed filing. The row exists so the
    review queue is explicit rather than implied by the absence of a decision.

    Idempotent: one classification per (evidence, classifier version), so a
    re-run over already-classified evidence changes nothing.
    """
    evidence_rows = session.execute(select(Evidence)).scalars().all()
    classified = set(
        session.execute(
            select(Classification.evidence_id).where(
                Classification.classifier_version == CLASSIFIER_VERSION
            )
        )
        .scalars()
        .all()
    )

    created = 0
    for evidence in evidence_rows:
        if evidence.id in classified:
            continue
        session.add(
            Classification(
                evidence_id=evidence.id,
                classifier_version=CLASSIFIER_VERSION,
                relationship_type=Relationship.UNDETERMINED,
                disposition=Disposition.NEEDS_REVIEW,
                confidence=NO_CONFIDENCE,
                requires_review=True,
                rationale=RATIONALE,
            )
        )
        created += 1

    logger.info(
        "classification run: version=%s scanned=%d created=%d unchanged=%d",
        CLASSIFIER_VERSION,
        len(evidence_rows),
        created,
        len(evidence_rows) - created,
    )

    return ClassificationRunResult(
        classifier_version=CLASSIFIER_VERSION,
        scanned=len(evidence_rows),
        created=created,
        unchanged=len(evidence_rows) - created,
    )


def read_review_queue(session: Session, limit: int) -> list[ReviewItem]:
    """Return the evidence awaiting a human decision, newest first."""
    rows = session.execute(
        select(Classification, Evidence)
        .join(Evidence, Evidence.id == Classification.evidence_id)
        .where(Classification.requires_review.is_(True))
        .order_by(Evidence.received_at.desc().nullslast())
        .limit(limit)
    ).all()

    return [
        ReviewItem(
            classification_id=classification.id,
            evidence_id=evidence.id,
            source_thread_id=evidence.source_thread_id,
            subject=evidence.subject,
            received_at=evidence.received_at,
            disposition=classification.disposition,
            rationale=classification.rationale,
        )
        for classification, evidence in rows
    ]
