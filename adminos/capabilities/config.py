import hashlib

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from adminos.capabilities.screens import ScreenConfig
from adminos.config import get_capabilities_path
from adminos.logging import get_logger


DEFAULT_CAPABILITIES_PATH = Path(__file__).resolve().parents[2] / "config" / "capabilities.yaml"

logger = get_logger(__name__)


class CapabilityConfigError(RuntimeError):
    """Raised when the capability configuration is missing or invalid."""


class UnknownCapability(KeyError):
    """Raised when a caller names a capability the configuration does not define."""


class ActionKind(StrEnum):
    """Every action a capability may be permitted to take.

    Actions are declared here and granted per capability, so a new action
    cannot reach a mailbox merely because code exists to perform it.
    """

    GMAIL_LABEL = "gmail.label"
    GMAIL_ARCHIVE = "gmail.archive"
    GMAIL_TRASH = "gmail.trash"
    GMAIL_DRAFT_REPLY = "gmail.draft_reply"
    GMAIL_SEND_DRAFT = "gmail.send_draft"
    MONDAY_CREATE_TASK = "monday.create_task"


class Recommendation(StrEnum):
    """The non-action outcomes a policy may recommend."""

    NEEDS_REVIEW = "needs_review"
    NO_ACTION = "no_action"


class PlaybookStep(StrEnum):
    """The deterministic steps a capability's playbook may run, in order."""

    COLLECT_EVIDENCE = "collect_evidence"
    RECOMMEND = "recommend"
    AWAIT_DECISION = "await_decision"
    PREPARE_ACTIONS = "prepare_actions"
    EXECUTE_APPROVED = "execute_approved"
    VERIFY = "verify"


ACTION_VALUES = {kind.value for kind in ActionKind}
RECOMMENDATION_VALUES = ACTION_VALUES | {outcome.value for outcome in Recommendation}

GMAIL_ACTIONS = {
    ActionKind.GMAIL_LABEL,
    ActionKind.GMAIL_ARCHIVE,
    ActionKind.GMAIL_TRASH,
    ActionKind.GMAIL_DRAFT_REPLY,
    ActionKind.GMAIL_SEND_DRAFT,
}
"""The actions that reach the mailbox, and so pass the Gmail kill switch."""

ACTION_ALIASES: dict[str, ActionKind] = {
    "archive_gmail_thread": ActionKind.GMAIL_ARCHIVE,
    "move_gmail_thread_to_trash": ActionKind.GMAIL_TRASH,
}
"""Spoken names for the two Gmail dispositions.

The stored action is `gmail.archive` or `gmail.trash`; these are the names a
reader sees and may send back. Accepting both means the audit keeps one name
for a thing while the contract can say it in words — and `delete` never
reaches an alias at all, because deleting is not what happens.
"""


def read_action_kind(value: str) -> ActionKind:
    """The action named, whether by its stored value or its spoken name."""
    alias = ACTION_ALIASES.get(value)
    if alias is not None:
        return alias
    return ActionKind(value)


class StrictModel(BaseModel):
    """Rejects unknown keys, so a typo in configuration fails loudly."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class GmailScope(StrictModel):
    labels: list[str] = Field(min_length=1)
    require_inbox: bool = True


class Playbook(StrictModel):
    id: str
    steps: list[PlaybookStep] = Field(min_length=1)

    def allows(self, step: PlaybookStep) -> bool:
        return step in self.steps


class MatchRule(StrictModel):
    """Conditions over retained metadata only; message bodies are never stored."""

    subject_contains: list[str] = []
    participant_domains: list[str] = []
    participants: list[str] = []
    older_than_days: int | None = Field(default=None, ge=0)
    newer_than_days: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_a_condition(self) -> Self:
        if not any(
            [
                self.subject_contains,
                self.participant_domains,
                self.participants,
                self.older_than_days is not None,
                self.newer_than_days is not None,
            ]
        ):
            raise ValueError("A rule with no condition would match everything.")
        return self


class RecommendationRule(StrictModel):
    id: str
    when: MatchRule
    recommend: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rationale: str
    aligns_with: list[str] = []

    @model_validator(mode="after")
    def check_recommendation(self) -> Self:
        if self.recommend not in RECOMMENDATION_VALUES:
            raise ValueError(f"{self.recommend!r} is not a recommendable outcome.")
        return self


class RecommendationPolicy(StrictModel):
    version: str
    default: str = Recommendation.NEEDS_REVIEW
    categories: list[str] = Field(min_length=1)
    rules: list[RecommendationRule] = []
    allow_ai_recommendation: bool = True
    min_ai_confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_default(self) -> Self:
        if self.default not in RECOMMENDATION_VALUES:
            raise ValueError(f"{self.default!r} is not a recommendable outcome.")
        return self


class ApprovalRules(StrictModel):
    auto_approve: list[ActionKind] = []
    min_confidence_for_auto: float = Field(default=1.0, ge=0.0, le=1.0)
    allow_bulk_decisions: bool = True


class ExecutionRules(StrictModel):
    """Which approved actions may actually reach the outside world.

    Separate from `allowed_actions` so that approving something and performing
    it are two grants rather than one: an action can be approvable and recorded
    while its executor is still unproven, and `GMAIL_WRITE_ENABLED` remains a
    kill switch over the top rather than the only gate.
    """

    permitted_actions: list[ActionKind] = []
    require_verification: bool = True

    def permits(self, action: ActionKind) -> bool:
        return action in self.permitted_actions


class CompletionRules(StrictModel):
    require_all_items_decided: bool = True
    require_executed_actions: bool = True


class LearningRules(StrictModel):
    scope: Literal["capability", "global", "none"] = "capability"
    record_decisions: bool = True
    record_message_content: bool = False
    allow_rule_learning: bool = True
    allow_automatable_rules: bool = False

    @model_validator(mode="after")
    def refuse_content_retention(self) -> Self:
        if self.record_message_content:
            raise ValueError(
                "Message content is never retained; see ADR-0003. Remove "
                "record_message_content or set it to false."
            )
        return self


class PresentationRules(StrictModel):
    """Which presentation contract renders this capability's review.

    Named rather than inlined so two capabilities may share a screen while it
    is young, and diverge by pointing at their own version later without any
    code change.
    """

    screen: str


class ObjectiveRules(StrictModel):
    default_keys: list[str] = []
    require_alignment: bool = False

    @model_validator(mode="after")
    def check_alignment_is_satisfiable(self) -> Self:
        if self.require_alignment and not self.default_keys:
            raise ValueError(
                "require_alignment needs default_keys, otherwise no action can ever "
                "satisfy it."
            )
        return self


class CapabilityConfig(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    enabled: bool = True
    position: int
    description: str | None = None
    gmail: GmailScope
    playbook: Playbook
    presentation: PresentationRules
    recommendation_policy: RecommendationPolicy
    allowed_actions: list[ActionKind] = []
    approval: ApprovalRules = ApprovalRules()
    execution: ExecutionRules = ExecutionRules()
    completion: CompletionRules = CompletionRules()
    learning: LearningRules = LearningRules()
    objectives: ObjectiveRules = ObjectiveRules()

    @model_validator(mode="after")
    def check_permissions_cover_every_action(self) -> Self:
        allowed = {action.value for action in self.allowed_actions}
        for rule in self.recommendation_policy.rules:
            if rule.recommend in ACTION_VALUES and rule.recommend not in allowed:
                raise ValueError(
                    f"Rule {rule.id!r} recommends {rule.recommend!r}, which "
                    f"{self.key!r} is not allowed to do."
                )
        if self.recommendation_policy.default in ACTION_VALUES:
            raise ValueError(
                f"{self.key!r} defaults to {self.recommendation_policy.default!r}; a "
                "default must not be an action, or unmatched mail would be acted on."
            )
        for action in self.approval.auto_approve:
            if action not in self.allowed_actions:
                raise ValueError(
                    f"{self.key!r} auto-approves {action.value!r} without being "
                    "allowed to do it."
                )
        for action in self.execution.permitted_actions:
            if action not in self.allowed_actions:
                raise ValueError(
                    f"{self.key!r} may execute {action.value!r} without being allowed "
                    "to approve it."
                )
        if (
            ActionKind.GMAIL_SEND_DRAFT in self.execution.permitted_actions
            and ActionKind.GMAIL_DRAFT_REPLY not in self.execution.permitted_actions
        ):
            raise ValueError(
                f"{self.key!r} may send a draft but not create one; a send is only "
                "ever the approval of a draft this capability wrote."
            )
        if self.learning.allow_automatable_rules and not self.learning.allow_rule_learning:
            raise ValueError(
                f"{self.key!r} allows automatable rules without allowing rule learning."
            )
        return self

    def permits(self, action: ActionKind) -> bool:
        return action in self.allowed_actions

    def may_execute(self, action: ActionKind) -> bool:
        return self.execution.permits(action)

    def auto_approves(self, action: ActionKind, confidence: float) -> bool:
        """Whether an action may execute without a human saying so."""
        return (
            action in self.approval.auto_approve
            and confidence >= self.approval.min_confidence_for_auto
        )


class CapabilitySet(StrictModel):
    version: str
    channel: str = "email"
    screens: list[ScreenConfig] = Field(min_length=1)
    capabilities: list[CapabilityConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def check_identity(self) -> Self:
        keys = [capability.key for capability in self.capabilities]
        if len(set(keys)) != len(keys):
            raise ValueError("Capability keys must be unique.")
        positions = [capability.position for capability in self.capabilities]
        if len(set(positions)) != len(positions):
            raise ValueError("Capability positions must be unique; they order the review.")
        screen_ids = [screen.id for screen in self.screens]
        if len(set(screen_ids)) != len(screen_ids):
            raise ValueError("Screen ids must be unique; they version the presentation.")
        return self

    @model_validator(mode="after")
    def check_screens_match_their_capabilities(self) -> Self:
        """A screen may not offer what its capability is not allowed to do.

        Otherwise the contract would advertise a button that the decision
        endpoint refuses, and the reader would learn what is permitted by being
        told no.
        """
        by_id = {screen.id: screen for screen in self.screens}
        for screen in self.screens:
            for label_key in screen.action_labels:
                if label_key not in RECOMMENDATION_VALUES:
                    raise ValueError(
                        f"Screen {screen.id!r} labels {label_key!r}, which is not a "
                        "recommendable outcome."
                    )

        for capability in self.capabilities:
            screen = by_id.get(capability.presentation.screen)
            if screen is None:
                known = ", ".join(sorted(by_id)) or "none"
                raise ValueError(
                    f"{capability.key!r} renders with screen "
                    f"{capability.presentation.screen!r}, which is not defined. "
                    f"Known screens: {known}."
                )
            for offered in screen.actions:
                if offered.action is None:
                    continue
                if offered.action not in ACTION_VALUES:
                    raise ValueError(
                        f"Screen {screen.id!r} offers {offered.action!r}, which is not "
                        "an action."
                    )
                if not capability.permits(ActionKind(offered.action)):
                    raise ValueError(
                        f"Screen {screen.id!r} offers {offered.action!r} to "
                        f"{capability.key!r}, which is not allowed to do it."
                    )
                if ActionKind(offered.action) in GMAIL_ACTIONS and not capability.may_execute(
                    ActionKind(offered.action)
                ):
                    raise ValueError(
                        f"Screen {screen.id!r} offers {offered.action!r} to "
                        f"{capability.key!r}, which may approve it but cannot execute "
                        "it, so the mailbox would never change."
                    )
            if not capability.approval.allow_bulk_decisions and any(
                offered.scope == "group" for offered in screen.actions
            ):
                raise ValueError(
                    f"Screen {screen.id!r} offers a whole-group decision to "
                    f"{capability.key!r}, which does not take bulk decisions."
                )
        return self


@dataclass(frozen=True)
class LoadedCapabilities:
    """A parsed configuration file, with the digest that identifies it.

    Runs record both the declared version and the digest, so a run's behaviour
    can be explained by the exact configuration that produced it even after the
    file changes.
    """

    version: str
    digest: str
    channel: str
    capabilities: tuple[CapabilityConfig, ...]
    screens: tuple[ScreenConfig, ...] = ()

    def enabled(self) -> tuple[CapabilityConfig, ...]:
        return tuple(
            sorted(
                (capability for capability in self.capabilities if capability.enabled),
                key=lambda capability: capability.position,
            )
        )

    def get(self, key: str) -> CapabilityConfig:
        for capability in self.capabilities:
            if capability.key == key:
                return capability
        raise UnknownCapability(f"No capability named {key!r} is configured.")

    def screen_for(self, capability: CapabilityConfig) -> ScreenConfig:
        """The presentation contract this capability is rendered with."""
        for screen in self.screens:
            if screen.id == capability.presentation.screen:
                return screen
        raise UnknownCapability(
            f"No screen named {capability.presentation.screen!r} is configured."
        )


_cache: dict[Path, tuple[float, LoadedCapabilities]] = {}


def resolve_capabilities_path() -> Path:
    configured = get_capabilities_path()
    if configured is None:
        return DEFAULT_CAPABILITIES_PATH
    return Path(configured)


def load_capabilities(path: Path | None = None) -> LoadedCapabilities:
    """Read, validate, and cache the capability configuration.

    Cached on the file's modification time so an edited file is picked up
    without a restart, and a valid file is not re-parsed on every request.
    """
    resolved = (path or resolve_capabilities_path()).resolve()
    try:
        modified_at = resolved.stat().st_mtime
    except OSError as exc:
        raise CapabilityConfigError(f"No capability configuration at {resolved}.") from exc

    cached = _cache.get(resolved)
    if cached is not None and cached[0] == modified_at:
        return cached[1]

    loaded = parse_capabilities(resolved.read_bytes())
    _cache[resolved] = (modified_at, loaded)
    logger.info(
        "capability configuration loaded: version=%s digest=%s enabled=%d",
        loaded.version,
        loaded.digest[:12],
        len(loaded.enabled()),
    )
    return loaded


def parse_capabilities(raw: bytes) -> LoadedCapabilities:
    try:
        document: object = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise CapabilityConfigError(f"Capability configuration is not valid YAML: {exc}") from exc

    if not isinstance(document, dict):
        raise CapabilityConfigError("Capability configuration must be a mapping.")

    try:
        parsed = CapabilitySet.model_validate(document)
    except ValidationError as exc:
        raise CapabilityConfigError(f"Capability configuration is invalid: {exc}") from exc

    return LoadedCapabilities(
        version=parsed.version,
        digest=hashlib.sha256(raw).hexdigest(),
        channel=parsed.channel,
        capabilities=tuple(parsed.capabilities),
        screens=tuple(parsed.screens),
    )


def clear_cache() -> None:
    _cache.clear()
