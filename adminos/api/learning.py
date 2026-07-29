from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from adminos.api.deps import read_capability, read_capability_config
from adminos.api.security import require_api_key
from adminos.capabilities.config import ActionKind, MatchRule
from adminos.db.engine import DatabaseNotConfigured, session_scope
from adminos.db.models import CandidateRule, JsonObject, LearningEvent
from adminos.domain.learning import LearningKind, read_learning_events
from adminos.domain.review import HUMAN_ACTOR
from adminos.domain.rules import (
    HUMAN_SOURCE,
    TRANSITIONS,
    RuleNotFound,
    RuleRefused,
    RuleState,
    dump_match,
    read_rule,
    read_rules,
    record_rule,
    transition_rule,
)
from adminos.logging import get_logger


logger = get_logger(__name__)
router = APIRouter(prefix="/learning", tags=["learning"])

DEFAULT_EVENT_LIMIT = 50
MAX_EVENT_LIMIT = 500


class ProposeRuleRequest(BaseModel):
    """A deterministic rule, written out in full before it can be confirmed.

    Conditions are metadata only, and a rule with no condition is refused: it
    would match every thread the capability sees.
    """

    capability_key: str
    match: MatchRule
    action: ActionKind
    action_params: JsonObject | None = Field(
        default=None,
        description=(
            "What the action needs: a move names its folder here as "
            '{"label": "Later"}, and the folder must be one the capability '
            "files mail in."
        ),
    )
    rationale: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class RuleTransitionRequest(BaseModel):
    reason: str | None = None
    confirm: bool = False


class RuleResponse(BaseModel):
    rule_id: str
    capability_key: str
    state: str
    match: JsonObject
    action: str
    action_params: JsonObject | None
    rationale: str
    confidence: float
    observed_count: int
    source: str
    policy_version: str | None
    active: bool
    may_execute_without_approval: bool
    next_states: list[str]
    proposed_by: str | None
    proposed_at: datetime | None
    confirmed_by: str | None
    confirmed_at: datetime | None
    automatable_at: datetime | None
    retired_at: datetime | None
    retired_reason: str | None


class RulesResponse(BaseModel):
    rules: list[RuleResponse]


class LearningEventResponse(BaseModel):
    event_id: str
    capability_key: str
    kind: str
    recommended: str
    chosen: str
    recommendation_source: str
    policy_version: str
    rule_id: str | None
    model_version: str | None
    signals: JsonObject | None
    note: str | None
    candidate_rule_id: str | None
    item_id: str | None
    run_id: str | None
    at: datetime


class LearningEventsResponse(BaseModel):
    events: list[LearningEventResponse]


@router.get("/events", response_model=LearningEventsResponse)
def list_learning_events(
    capability_key: str | None = None,
    kind: LearningKind | None = None,
    limit: int = Query(default=DEFAULT_EVENT_LIMIT, ge=1, le=MAX_EVENT_LIMIT),
    _: None = Depends(require_api_key),
) -> LearningEventsResponse:
    """What the review has been taught, as evidence rather than as behaviour."""
    with open_session() as session:
        events = read_learning_events(
            session,
            capability_key=capability_key,
            kinds=[kind] if kind is not None else None,
            limit=limit,
        )
        return LearningEventsResponse(
            events=[build_event_response(event) for event in events]
        )


@router.get("/rules", response_model=RulesResponse)
def list_rules(
    capability_key: str | None = None,
    state: RuleState | None = None,
    _: None = Depends(require_api_key),
) -> RulesResponse:
    """Every candidate rule and where it stands."""
    with open_session() as session:
        rules = read_rules(
            session,
            capability_key=capability_key,
            states=[state] if state is not None else None,
        )
        return RulesResponse(rules=[build_rule_response(rule) for rule in rules])


@router.post("/rules", response_model=RuleResponse, status_code=201)
def propose_rule(
    request: ProposeRuleRequest,
    _: None = Depends(require_api_key),
) -> RuleResponse:
    """Propose a rule. Proposing does not activate it.

    A proposed rule changes nothing about the review. It exists to be read in
    full — its exact conditions and the single action it would take — and then
    confirmed or refused.
    """
    loaded = read_capability_config()
    capability = read_capability(loaded, request.capability_key)

    with open_session() as session:
        try:
            rule = record_rule(
                session,
                capability,
                request.match,
                request.action,
                rationale=request.rationale,
                state=RuleState.PROPOSED,
                source=HUMAN_SOURCE,
                params=request.action_params,
                confidence=request.confidence,
                actor=HUMAN_ACTOR,
            )
        except RuleRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return build_rule_response(rule)


@router.get("/rules/{rule_id}", response_model=RuleResponse)
def get_rule(rule_id: str, _: None = Depends(require_api_key)) -> RuleResponse:
    """One rule, with the exact conditions it matches and what it would do."""
    with open_session() as session:
        return build_rule_response(lookup_rule(session, rule_id))


@router.post("/rules/{rule_id}/confirm", response_model=RuleResponse)
def confirm_rule(
    rule_id: str,
    request: RuleTransitionRequest,
    _: None = Depends(require_api_key),
) -> RuleResponse:
    """Activate a proposed rule for future recommendations.

    A confirmed rule advises. It does not act: that takes a separate
    promotion, so agreeing with a rule is never the same as licensing it to
    run unattended.
    """
    return move_rule(rule_id, RuleState.CONFIRMED, request)


@router.post("/rules/{rule_id}/promote", response_model=RuleResponse)
def promote_rule(
    rule_id: str,
    request: RuleTransitionRequest,
    _: None = Depends(require_api_key),
) -> RuleResponse:
    """Let a confirmed rule act without asking first.

    The narrowest grant in the system, and still not the last gate: the
    capability must permit the action's execution, and Gmail writes must be
    enabled, before anything happens.
    """
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail=(
                "Promotion lets this rule act without approval. Send confirm=true to "
                "grant that."
            ),
        )
    return move_rule(rule_id, RuleState.AUTOMATABLE, request)


@router.post("/rules/{rule_id}/retire", response_model=RuleResponse)
def retire_rule(
    rule_id: str,
    request: RuleTransitionRequest,
    _: None = Depends(require_api_key),
) -> RuleResponse:
    """Retire a rule. It stops recommending and stops acting, permanently."""
    return move_rule(rule_id, RuleState.RETIRED, request)


def move_rule(rule_id: str, target: RuleState, request: RuleTransitionRequest) -> RuleResponse:
    loaded = read_capability_config()
    with open_session() as session:
        rule = lookup_rule(session, rule_id)
        capability = read_capability(loaded, rule.capability_key)
        try:
            moved = transition_rule(
                session,
                capability,
                rule,
                target,
                actor=HUMAN_ACTOR,
                reason=request.reason,
            )
        except RuleRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return build_rule_response(moved)


@contextmanager
def open_session() -> Iterator[Session]:
    """Open a session, reporting an unconfigured database as 503 rather than 500."""
    try:
        with session_scope() as session:
            yield session
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def lookup_rule(session: Session, rule_id: str) -> CandidateRule:
    try:
        return read_rule(session, rule_id)
    except RuleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def build_rule_response(rule: CandidateRule) -> RuleResponse:
    state = RuleState(rule.state)
    return RuleResponse(
        rule_id=rule.id,
        capability_key=rule.capability_key,
        state=state.value,
        match=dump_match(MatchRule.model_validate(rule.match_conditions)),
        action=rule.action,
        action_params=rule.action_params,
        rationale=rule.rationale,
        confidence=rule.confidence,
        observed_count=rule.observed_count,
        source=rule.source,
        policy_version=rule.policy_version,
        active=state in {RuleState.CONFIRMED, RuleState.AUTOMATABLE},
        may_execute_without_approval=state is RuleState.AUTOMATABLE,
        next_states=sorted(target.value for target in TRANSITIONS[state]),
        proposed_by=rule.proposed_by,
        proposed_at=rule.proposed_at,
        confirmed_by=rule.confirmed_by,
        confirmed_at=rule.confirmed_at,
        automatable_at=rule.automatable_at,
        retired_at=rule.retired_at,
        retired_reason=rule.retired_reason,
    )


def build_event_response(event: LearningEvent) -> LearningEventResponse:
    return LearningEventResponse(
        event_id=event.id,
        capability_key=event.capability_key,
        kind=event.kind,
        recommended=event.recommended,
        chosen=event.chosen,
        recommendation_source=event.recommendation_source,
        policy_version=event.policy_version,
        rule_id=event.rule_id,
        model_version=event.model_version,
        signals=event.signals,
        note=event.note,
        candidate_rule_id=event.candidate_rule_id,
        item_id=event.item_id,
        run_id=event.run_id,
        at=event.created_at,
    )
