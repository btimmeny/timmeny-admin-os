"""Reading, writing, previewing and moving rules.

Every route here is one step of the life a rule has, and no route does two of
them. Testing changes no mail; confirming changes no review; activating is the
only thing that puts a rule to work, and it is its own request.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from adminos.api.deps import read_capability_config
from adminos.api.security import require_api_key
from adminos.db.engine import DatabaseNotConfigured, session_scope
from adminos.domain.conditions import ConditionError, ConditionRefused, describe_fields
from adminos.domain.decisions import HUMAN_ACTOR
from adminos.domain.patterns import suggest_subject_conditions
from adminos.domain.rule_testing import (
    DEFAULT_SAMPLE,
    MAXIMUM_SAMPLE,
    Report,
    PreviewSource,
    preview_draft,
    preview_rule,
)
from adminos.domain.rulebook import (
    TRANSITIONS,
    RuleDraft,
    RuleError,
    RuleStatus,
    RuleType,
    describe_rule_types,
)
from adminos.domain.rulebook_store import (
    RuleNotFound,
    RuleRecord,
    amend_rule,
    move_rule,
    propose_rule,
    read_rule,
    read_rule_events,
    read_rule_versions,
    read_rules,
)
from adminos.logging import get_logger


logger = get_logger(__name__)
router = APIRouter(prefix="/rules", tags=["rules"])


class PreviewRequest(BaseModel):
    """Which mail to try a rule against. Nothing here changes anything."""

    source: PreviewSource = PreviewSource.CURRENT_SNAPSHOT
    thread_ids: list[str] = []
    subjects: list[str] = []
    limit: int = Field(default=DEFAULT_SAMPLE, ge=1, le=MAXIMUM_SAMPLE)


class DraftPreviewRequest(PreviewRequest):
    """A rule that has not been written down yet, tried before it is."""

    draft: RuleDraft


class MoveRequest(BaseModel):
    reason: str | None = None
    confirm: bool = False


class SuggestRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=500)


class SuggestionResponse(BaseModel):
    key: str
    label: str
    summary: str
    catches: str
    misses: str
    field: str
    operator: str
    value: str


class SuggestionsResponse(BaseModel):
    subject: str
    readings: list[SuggestionResponse]


class FieldResponse(BaseModel):
    key: str
    label: str
    kind: str
    reads: str
    operators: list[str]
    narrows: bool


class FieldsResponse(BaseModel):
    fields: list[FieldResponse]


class RuleTypeResponse(BaseModel):
    rule_type: str
    label: str
    source: str
    effects: list[str]
    available: bool
    unavailable_because: str | None


class RuleTypesResponse(BaseModel):
    rule_types: list[RuleTypeResponse]


class VersionResponse(BaseModel):
    version_id: str
    number: int
    name: str
    priority: int
    summary: list[str]
    change_reason: str | None
    created_by: str
    created_at: datetime


class EventResponse(BaseModel):
    kind: str
    from_status: str | None
    to_status: str | None
    actor: str
    at: datetime


class RuleResponse(BaseModel):
    rule_id: str
    rule_type: str
    capability_key: str | None
    status: str
    in_force: bool
    next_statuses: list[str]
    version: VersionResponse
    summary: list[str]
    match: dict[str, object]
    effects: list[dict[str, object]]
    constraints: dict[str, object]
    created_by: str
    created_at: datetime
    confirmed_at: datetime | None
    activated_at: datetime | None
    retired_at: datetime | None


class RulesResponse(BaseModel):
    rules: list[RuleResponse]


class RuleHistoryResponse(BaseModel):
    rule: RuleResponse
    versions: list[VersionResponse]
    events: list[EventResponse]


class PreviewResponse(BaseModel):
    reference: str
    summary: str
    reasons: list[str]
    captured: dict[str, str]
    effects: list[str]
    reviewed_as: list[str]
    requires_confirmation: bool


class ReportResponse(BaseModel):
    test_run_id: str
    rule_id: str | None
    version_number: int | None
    source: str
    sampled_from: str
    counts: dict[str, int]
    matched: list[PreviewResponse]
    unmatched: list[str]
    warnings: list[str]
    false_positive_candidates: list[str]
    false_negative_candidates: list[str]
    executed: bool
    ran_at: datetime


class TestedRuleResponse(BaseModel):
    rule: RuleResponse
    report: ReportResponse


@router.get("", response_model=RulesResponse)
def list_rules(
    rule_type: RuleType | None = None,
    status: RuleStatus | None = None,
    capability_key: str | None = None,
    _: None = Depends(require_api_key),
) -> RulesResponse:
    """Every rule matching the filters, in the order conflicts are settled by."""
    with open_session() as session:
        records = read_rules(
            session, rule_type=rule_type, status=status, capability_key=capability_key
        )
        return RulesResponse(rules=[build_rule(record) for record in records])


@router.get("/fields", response_model=FieldsResponse)
def list_fields(_: None = Depends(require_api_key)) -> FieldsResponse:
    """What a rule may match on, and what stands behind each field."""
    return FieldsResponse(
        fields=[
            FieldResponse(
                key=spec.key,
                label=spec.label,
                kind=spec.kind.value,
                reads=spec.source,
                operators=sorted(operator.value for operator in spec.operators()),
                narrows=spec.narrowing,
            )
            for spec in describe_fields()
        ]
    )


@router.get("/types", response_model=RuleTypesResponse)
def list_rule_types(_: None = Depends(require_api_key)) -> RuleTypesResponse:
    """Every kind of rule, and for the ones nothing can carry out, why not."""
    return RuleTypesResponse(
        rule_types=[
            RuleTypeResponse(
                rule_type=spec.rule_type.value,
                label=spec.label,
                source=spec.source,
                effects=sorted(effect.value for effect in spec.effects),
                available=spec.available,
                unavailable_because=spec.unavailable_because,
            )
            for spec in describe_rule_types()
        ]
    )


@router.post("/subject-readings", response_model=SuggestionsResponse)
def suggest_readings(
    request: SuggestRequest, _: None = Depends(require_api_key)
) -> SuggestionsResponse:
    """The ways one subject could be read as a rule, narrowest first.

    Suggestions only. Which part of a subject varies is a guess about the
    mailbox, and it is answered by previewing rather than by picking.
    """
    try:
        readings = suggest_subject_conditions(request.subject)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SuggestionsResponse(
        subject=request.subject,
        readings=[
            SuggestionResponse(
                key=reading.key,
                label=reading.label,
                summary=reading.summary,
                catches=reading.catches,
                misses=reading.misses,
                field=reading.condition.field,
                operator=reading.condition.operator.value,
                value=reading.condition.value,
            )
            for reading in readings
        ],
    )


@router.post("", response_model=RuleResponse, status_code=201)
def write_rule(draft: RuleDraft, _: None = Depends(require_api_key)) -> RuleResponse:
    """Write a rule down. It is proposed, and it changes nothing."""
    loaded = read_capability_config()
    with open_session() as session:
        record = refuse_gracefully(
            lambda: propose_rule(session, draft=draft, loaded=loaded, actor=HUMAN_ACTOR)
        )
        return build_rule(record)


@router.post("/preview", response_model=ReportResponse)
def preview_unwritten_rule(
    request: DraftPreviewRequest, _: None = Depends(require_api_key)
) -> ReportResponse:
    """Try a rule that has not been written down, against real mail.

    Reads retained metadata and writes nothing at all — not even that the test
    happened, because there is no rule yet for it to have happened to.
    """
    with open_session() as session:
        report = refuse_gracefully(
            lambda: preview_draft(
                session,
                draft=request.draft,
                source=request.source,
                thread_ids=request.thread_ids,
                subjects=request.subjects,
                limit=request.limit,
            )
        )
        return build_report(report)


@router.get("/{rule_id}", response_model=RuleHistoryResponse)
def get_rule(rule_id: str, _: None = Depends(require_api_key)) -> RuleHistoryResponse:
    """One rule: what it says now, every version it has said, and every move."""
    with open_session() as session:
        record = lookup(session, rule_id)
        return RuleHistoryResponse(
            rule=build_rule(record),
            versions=[
                VersionResponse(
                    version_id=version.id,
                    number=version.number,
                    name=version.name,
                    priority=version.priority,
                    summary=list(version.summary),
                    change_reason=version.change_reason,
                    created_by=version.created_by,
                    created_at=version.created_at,
                )
                for version in read_rule_versions(session, rule_id)
            ],
            events=[
                EventResponse(
                    kind=event.kind,
                    from_status=event.from_status,
                    to_status=event.to_status,
                    actor=event.actor,
                    at=event.created_at,
                )
                for event in read_rule_events(session, rule_id)
            ],
        )


@router.post("/{rule_id}/versions", response_model=RuleResponse)
def amend(rule_id: str, draft: RuleDraft, _: None = Depends(require_api_key)) -> RuleResponse:
    """Write the next version of a rule.

    The rule goes back to proposed, and has to be tested and agreed to again:
    what was confirmed was the version, not the name.
    """
    loaded = read_capability_config()
    with open_session() as session:
        lookup(session, rule_id)
        record = refuse_gracefully(
            lambda: amend_rule(
                session, rule_id=rule_id, draft=draft, loaded=loaded, actor=HUMAN_ACTOR
            )
        )
        return build_rule(record)


@router.post("/{rule_id}/test", response_model=TestedRuleResponse)
def test_rule(
    rule_id: str, request: PreviewRequest, _: None = Depends(require_api_key)
) -> TestedRuleResponse:
    """Try a rule against mail, and record that it was tried.

    A proposed rule becomes tested by this, which is the only way it can reach
    confirmed. No mailbox and no board is touched.
    """
    with open_session() as session:
        lookup(session, rule_id)
        record, report = refuse_gracefully(
            lambda: preview_rule(
                session,
                rule_id=rule_id,
                source=request.source,
                thread_ids=request.thread_ids,
                subjects=request.subjects,
                limit=request.limit,
                actor=HUMAN_ACTOR,
            )
        )
        return TestedRuleResponse(rule=build_rule(record), report=build_report(report))


@router.post("/{rule_id}/confirm", response_model=RuleResponse)
def confirm(rule_id: str, request: MoveRequest, _: None = Depends(require_api_key)) -> RuleResponse:
    """Agree that a rule is right. It still does not run: activate it for that."""
    return move(rule_id, RuleStatus.CONFIRMED, request)


@router.post("/{rule_id}/activate", response_model=RuleResponse)
def activate(
    rule_id: str, request: MoveRequest, _: None = Depends(require_api_key)
) -> RuleResponse:
    """Put a confirmed rule to work. From now it shapes what the review says."""
    return move(rule_id, RuleStatus.ACTIVE, request)


@router.post("/{rule_id}/pause", response_model=RuleResponse)
def pause(rule_id: str, request: MoveRequest, _: None = Depends(require_api_key)) -> RuleResponse:
    """Stop a rule working without retiring it. It can be resumed."""
    return move(rule_id, RuleStatus.PAUSED, request)


@router.post("/{rule_id}/resume", response_model=RuleResponse)
def resume(rule_id: str, request: MoveRequest, _: None = Depends(require_api_key)) -> RuleResponse:
    """Put a paused rule back to work, at the version it was paused at."""
    return move(rule_id, RuleStatus.ACTIVE, request)


@router.post("/{rule_id}/retire", response_model=RuleResponse)
def retire(rule_id: str, request: MoveRequest, _: None = Depends(require_api_key)) -> RuleResponse:
    """Retire a rule, permanently. There is no way back from this one."""
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail=(
                "Retiring is permanent: a retired rule cannot be brought back or "
                "amended. Send confirm=true to retire it, or pause it instead."
            ),
        )
    return move(rule_id, RuleStatus.RETIRED, request)


def move(rule_id: str, to: RuleStatus, request: MoveRequest) -> RuleResponse:
    with open_session() as session:
        lookup(session, rule_id)
        record = refuse_gracefully(
            lambda: move_rule(
                session, rule_id=rule_id, to=to, actor=HUMAN_ACTOR, reason=request.reason
            )
        )
        return build_rule(record)


@contextmanager
def open_session() -> Iterator[Session]:
    """Open a session, reporting an unconfigured database as 503 rather than 500."""
    try:
        with session_scope() as session:
            yield session
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def lookup(session: Session, rule_id: str) -> RuleRecord:
    try:
        return read_rule(session, rule_id)
    except RuleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def refuse_gracefully[T](work: Callable[[], T]) -> T:
    """A refusal is the answer to the question, not a failure of the service."""
    try:
        return work()
    except (RuleError, ConditionError, ConditionRefused) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def build_rule(record: RuleRecord) -> RuleResponse:
    status = RuleStatus(record.rule.status)
    version = record.version
    return RuleResponse(
        rule_id=record.rule.id,
        rule_type=record.rule.rule_type,
        capability_key=record.rule.capability_key,
        status=status.value,
        in_force=status in {RuleStatus.ACTIVE, RuleStatus.AUTOMATABLE},
        next_statuses=sorted(target.value for target in TRANSITIONS[status]),
        version=VersionResponse(
            version_id=version.id,
            number=version.number,
            name=version.name,
            priority=version.priority,
            summary=list(version.summary),
            change_reason=version.change_reason,
            created_by=version.created_by,
            created_at=version.created_at,
        ),
        summary=list(version.summary),
        match=dict(version.match_conditions),
        effects=[dict(effect) for effect in version.effects],
        constraints=dict(version.constraints),
        created_by=record.rule.created_by,
        created_at=record.rule.created_at,
        confirmed_at=record.rule.confirmed_at,
        activated_at=record.rule.activated_at,
        retired_at=record.rule.retired_at,
    )


def build_report(report: Report) -> ReportResponse:
    return ReportResponse(
        test_run_id=report.test_run_id,
        rule_id=report.rule_id,
        version_number=report.version_number,
        source=report.source.value,
        sampled_from=report.sampled_from,
        counts=report.counts(),
        matched=[
            PreviewResponse(
                reference=preview.reference,
                summary=preview.summary,
                reasons=list(preview.reasons),
                captured=dict(preview.captured),
                effects=list(preview.effects),
                reviewed_as=list(preview.groups),
                requires_confirmation=preview.requires_confirmation,
            )
            for preview in report.matched
        ],
        unmatched=list(report.unmatched),
        warnings=list(report.warnings),
        false_positive_candidates=list(report.false_positives),
        false_negative_candidates=list(report.false_negatives),
        executed=report.executed,
        ran_at=report.ran_at,
    )
