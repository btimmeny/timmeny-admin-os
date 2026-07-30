"""A rule as a record: what it is, what it does, and which version said so.

Three properties are what this module is for.

**A rule has one job.** A record that classifies, files, drafts, creates a
task and closes a thread is five decisions Brian can only agree to together.
Each rule declares a type, the type says which kind of effect it may carry,
and a rule carrying an effect its type does not own is refused.

**An edit is a new version, never a change to the old one.** Versions are
written once and kept; the rule points at its current one. Editing a rule that
was confirmed also drops it back to `proposed`, because the thing Brian agreed
to was that version of it, and a rule that keeps its standing across an edit is
a rule that can be changed into one he never saw.

**A type nobody implemented is unavailable, not broken.** The requirement names
fourteen rule types, and the engine can carry out three of them today. The rest
are declared, listed as unavailable, and refused at proposal — the same
treatment the session gives an activity with no data source behind it. A rule
that stores cleanly and never runs is worse than one that cannot be written.
"""

from __future__ import annotations

import hashlib
import json
import re

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from adminos.capabilities.config import (
    ACTION_VALUES,
    DESTINATION_PARAM,
    ActionKind,
    CapabilityConfig,
    Recommendation,
    read_action_kind,
)
from adminos.domain.conditions import ConditionGroup, check_breadth
from adminos.logging import get_logger


logger = get_logger(__name__)

DEFAULT_PRIORITY = 500

CAPTURE_NAMES = re.compile(r"\(\?P<(\w+)>")

RECOMMENDING_LEVEL = 1
"""The most a rule may be trusted with here: classify, and suggest.

Levels 2 to 4 — preselecting a decision, preparing, executing unattended —
need grants that do not exist yet, so a rule asking for one is refused rather
than stored as a promise nothing keeps.
"""


class RuleError(ValueError):
    """Raised when a rule is not something this system can hold."""


class RuleUnavailable(RuleError):
    """Raised when a rule names a type nothing here can carry out."""


class RuleType(StrEnum):
    ADMIN_FLOW = "admin_flow_rule"
    EMAIL_GROUP = "email_group_rule"
    EMAIL_CLASSIFICATION = "email_classification_rule"
    EMAIL_RECOMMENDATION = "email_recommendation_rule"
    EMAIL_FILING = "email_filing_rule"
    EMAIL_NOTIFICATION = "email_notification_rule"
    EMAIL_REPLY = "email_reply_rule"
    TODO_GROUP = "todo_group_rule"
    TODO_CLASSIFICATION = "todo_classification_rule"
    TODO_REMINDER = "todo_reminder_rule"
    TODO_NEXT_STEP = "todo_next_step_rule"
    TODO_DEPENDENCY = "todo_dependency_rule"
    TODO_COMPLETION = "todo_completion_rule"
    RECONCILIATION = "reconciliation_rule"


class EffectKind(StrEnum):
    ASSIGN_EMAIL_GROUP = "assign_email_group"
    RECOMMEND_ACTION = "recommend_action"
    SHOW_NOTIFICATION = "show_notification"


class EffectClass(StrEnum):
    """What an effect does to a review, which is not the same as what it says."""

    CLASSIFICATION = "classification"
    DISPLAY = "display"
    RECOMMENDATION = "recommendation"


EFFECT_CLASSES = {
    EffectKind.ASSIGN_EMAIL_GROUP: EffectClass.CLASSIFICATION,
    EffectKind.RECOMMEND_ACTION: EffectClass.RECOMMENDATION,
    EffectKind.SHOW_NOTIFICATION: EffectClass.DISPLAY,
}


@dataclass(frozen=True)
class RuleTypeSpec:
    """One kind of rule, and whether anything here can carry it out."""

    rule_type: RuleType
    label: str
    source: Literal["gmail", "monday", "both"]
    effects: frozenset[EffectKind]
    available: bool
    unavailable_because: str | None = None


def unavailable(
    rule_type: RuleType, label: str, source: Literal["gmail", "monday", "both"], because: str
) -> RuleTypeSpec:
    return RuleTypeSpec(
        rule_type=rule_type,
        label=label,
        source=source,
        effects=frozenset(),
        available=False,
        unavailable_because=because,
    )


NO_MONDAY_SCOPE = (
    "The Monday scope is not configured, so nothing here can read the items "
    "this rule would be about."
)
NO_MONDAY_REVIEW = "The Monday half of the review is not built yet."
NO_SCHEDULER = (
    "Nothing in this service wakes up on a schedule, so a reminder at a time "
    "could only be a promise."
)

RULE_TYPES: dict[RuleType, RuleTypeSpec] = {
    spec.rule_type: spec
    for spec in (
        RuleTypeSpec(
            rule_type=RuleType.EMAIL_CLASSIFICATION,
            label="Which group an email belongs in",
            source="gmail",
            effects=frozenset({EffectKind.ASSIGN_EMAIL_GROUP}),
            available=True,
        ),
        RuleTypeSpec(
            rule_type=RuleType.EMAIL_RECOMMENDATION,
            label="What to suggest for an email",
            source="gmail",
            effects=frozenset({EffectKind.RECOMMEND_ACTION}),
            available=True,
        ),
        RuleTypeSpec(
            rule_type=RuleType.EMAIL_NOTIFICATION,
            label="What to point out about an email",
            source="gmail",
            effects=frozenset({EffectKind.SHOW_NOTIFICATION}),
            available=True,
        ),
        RuleTypeSpec(
            rule_type=RuleType.EMAIL_FILING,
            label="Where to file an email",
            source="gmail",
            effects=frozenset({EffectKind.RECOMMEND_ACTION}),
            available=True,
        ),
        unavailable(
            RuleType.ADMIN_FLOW,
            "The order of the review",
            "both",
            "The order of a session is playbook configuration, changed through "
            "proposePlaybookChange rather than through a rule.",
        ),
        unavailable(
            RuleType.EMAIL_GROUP,
            "How a group of email is reviewed",
            "gmail",
            "Group behaviour is capability configuration today; it becomes a rule "
            "when group configuration does.",
        ),
        unavailable(
            RuleType.EMAIL_REPLY,
            "What to reply to an email",
            "gmail",
            "A drafted reply is written per thread and approved per thread; no rule "
            "writes one.",
        ),
        unavailable(RuleType.TODO_GROUP, "How Monday work is grouped", "monday", NO_MONDAY_SCOPE),
        unavailable(
            RuleType.TODO_CLASSIFICATION, "What a Monday item is", "monday", NO_MONDAY_SCOPE
        ),
        unavailable(RuleType.TODO_REMINDER, "When to be reminded", "monday", NO_SCHEDULER),
        unavailable(
            RuleType.TODO_NEXT_STEP, "What follows a Monday item", "monday", NO_MONDAY_REVIEW
        ),
        unavailable(
            RuleType.TODO_DEPENDENCY, "What a Monday item waits on", "monday", NO_MONDAY_REVIEW
        ),
        unavailable(
            RuleType.TODO_COMPLETION, "What finishing a Monday item means", "monday",
            NO_MONDAY_REVIEW,
        ),
        unavailable(
            RuleType.RECONCILIATION,
            "How email and Monday work are matched up",
            "both",
            "Reconciliation runs after both halves of the review, and the Monday half "
            "is not built yet.",
        ),
    )
}


class RuleStatus(StrEnum):
    """Where a rule stands. Only `active` and `automatable` change a review."""

    OBSERVED = "observed"
    PROPOSED = "proposed"
    TESTED = "tested"
    CONFIRMED = "confirmed"
    ACTIVE = "active"
    AUTOMATABLE = "automatable"
    PAUSED = "paused"
    RETIRED = "retired"


EFFECTIVE_STATUSES = {RuleStatus.ACTIVE, RuleStatus.AUTOMATABLE}
"""The two standings in which a rule shapes what a review says.

`confirmed` is deliberately not one of them: agreeing that a rule is right is
not the same act as putting it to work, and the requirement separates them.
"""

TRANSITIONS: dict[RuleStatus, set[RuleStatus]] = {
    RuleStatus.OBSERVED: {RuleStatus.PROPOSED, RuleStatus.RETIRED},
    RuleStatus.PROPOSED: {RuleStatus.TESTED, RuleStatus.RETIRED},
    RuleStatus.TESTED: {RuleStatus.CONFIRMED, RuleStatus.PROPOSED, RuleStatus.RETIRED},
    RuleStatus.CONFIRMED: {RuleStatus.ACTIVE, RuleStatus.RETIRED},
    RuleStatus.ACTIVE: {RuleStatus.PAUSED, RuleStatus.AUTOMATABLE, RuleStatus.RETIRED},
    RuleStatus.AUTOMATABLE: {RuleStatus.ACTIVE, RuleStatus.PAUSED, RuleStatus.RETIRED},
    RuleStatus.PAUSED: {RuleStatus.ACTIVE, RuleStatus.RETIRED},
    RuleStatus.RETIRED: set(),
}
"""Every legal move.

Confirming needs a test first, because a rule nobody previewed is a rule
nobody has seen the consequences of. Activating is a separate act from
confirming. Retiring is final: a rule that can come back from retirement is a
rule whose history has a hole in it.
"""


class EventKind(StrEnum):
    PROPOSED = "proposed"
    AMENDED = "amended"
    TESTED = "tested"
    CONFIRMED = "confirmed"
    ACTIVATED = "activated"
    PROMOTED = "promoted"
    PAUSED = "paused"
    RESUMED = "resumed"
    RETIRED = "retired"


TRANSITION_EVENTS = {
    RuleStatus.PROPOSED: EventKind.AMENDED,
    RuleStatus.TESTED: EventKind.TESTED,
    RuleStatus.CONFIRMED: EventKind.CONFIRMED,
    RuleStatus.ACTIVE: EventKind.ACTIVATED,
    RuleStatus.AUTOMATABLE: EventKind.PROMOTED,
    RuleStatus.PAUSED: EventKind.PAUSED,
    RuleStatus.RETIRED: EventKind.RETIRED,
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Effect(StrictModel):
    """One thing a rule does, and what kind of thing that is.

    `action` and `params` belong to a recommendation; `group_key` to a
    classification; `message` to something shown. Each is checked against the
    kind, so an effect cannot carry a field the kind has no use for.
    """

    kind: EffectKind
    group_key: str | None = None
    action: str | None = None
    params: dict[str, str] = {}
    message: str | None = None

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        if self.kind is EffectKind.ASSIGN_EMAIL_GROUP:
            if not self.group_key:
                raise ValueError("Putting an email in a group means naming the group.")
            if self.action or self.params or self.message:
                raise ValueError("Putting an email in a group does nothing else.")
        if self.kind is EffectKind.RECOMMEND_ACTION:
            if not self.action:
                raise ValueError("A recommendation names what it recommends.")
            if self.action not in ACTION_VALUES:
                raise ValueError(
                    f"{self.action!r} is not an action. "
                    f"The actions are: {', '.join(sorted(ACTION_VALUES))}."
                )
            if self.group_key or self.message:
                raise ValueError("A recommendation recommends, and nothing else.")
            if read_action_kind(self.action) is ActionKind.GMAIL_MOVE and not self.params.get(
                DESTINATION_PARAM
            ):
                raise ValueError(
                    "Filing mail means naming the folder: a move without a "
                    f"{DESTINATION_PARAM!r} is not something anyone can agree to."
                )
        if self.kind is EffectKind.SHOW_NOTIFICATION:
            if not self.message:
                raise ValueError("Pointing something out means saying what.")
            if self.action or self.params or self.group_key:
                raise ValueError("Pointing something out changes nothing.")
        return self

    def effect_class(self) -> EffectClass:
        return EFFECT_CLASSES[self.kind]

    def describes(self) -> str:
        if self.kind is EffectKind.ASSIGN_EMAIL_GROUP:
            return f"put it in the {self.group_key} group"
        if self.kind is EffectKind.SHOW_NOTIFICATION:
            return f"say {self.message!r}"
        destination = self.params.get(DESTINATION_PARAM)
        if destination:
            return f"recommend filing it in {destination!r}"
        return f"recommend {self.action}"

    def placeholders(self) -> set[str]:
        """The capture names a notification's wording expects to be given."""
        if self.message is None:
            return set()
        return {
            piece.split("}}")[0].strip()
            for piece in self.message.split("{{")[1:]
            if "}}" in piece
        }


class Constraints(StrictModel):
    """What a rule may never do on its own, written down with the rule.

    Defaults are the strictest reading, and the automation level is capped
    where the grants stop: a rule cannot store a claim to act unattended
    before there is anything that could grant it.
    """

    auto_execute: bool = False
    requires_decision: bool = True
    requires_preparation: bool = True
    requires_execution_confirmation: bool = True
    requires_verification: bool = True
    automation_level: int = Field(default=RECOMMENDING_LEVEL, ge=0, le=4)
    maximum_items_per_run: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check_nothing_is_granted_early(self) -> Self:
        if self.auto_execute:
            raise ValueError(
                "No rule executes on its own. Automatic execution is a separate "
                "grant, per action and per condition, and it does not exist yet."
            )
        if self.automation_level > RECOMMENDING_LEVEL:
            raise ValueError(
                f"Level {self.automation_level} lets a rule do more than suggest, "
                f"which needs a grant that does not exist yet. Level "
                f"{RECOMMENDING_LEVEL} classifies and recommends."
            )
        if not (
            self.requires_decision
            and self.requires_preparation
            and self.requires_execution_confirmation
            and self.requires_verification
        ):
            raise ValueError(
                "Deciding, preparing, confirming and verifying are the review's "
                "safeguards, not a rule's to waive."
            )
        return self


class RuleDraft(StrictModel):
    """A rule as written, before it is anything.

    Holding this apart from the stored record is what makes an amendment a
    version rather than an edit: the draft is what Brian wrote, and the record
    is the history of what he wrote.
    """

    name: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=1000)
    rule_type: RuleType
    capability_key: str | None = None
    priority: int = Field(default=DEFAULT_PRIORITY, ge=1, le=1000)
    match: ConditionGroup
    effects: list[Effect] = Field(min_length=1)
    constraints: Constraints = Constraints()
    positive_examples: list[str] = []
    negative_examples: list[str] = []
    change_reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def check_effects_belong_to_the_type(self) -> Self:
        spec = RULE_TYPES[self.rule_type]
        if not spec.available:
            raise ValueError(
                f"{self.rule_type} is not something this system can carry out. "
                f"{spec.unavailable_because}"
            )
        for effect in self.effects:
            if effect.kind not in spec.effects:
                raise ValueError(
                    f"A {self.rule_type} does not {effect.kind}. It may: "
                    f"{', '.join(sorted(spec.effects))}."
                )
        if len({effect.kind for effect in self.effects}) != len(self.effects):
            raise ValueError(
                "A rule does one thing. Two effects of the same kind are two rules."
            )
        return self

    @model_validator(mode="after")
    def check_wording_can_be_filled_in(self) -> Self:
        """A message may only name values the match actually captures."""
        available = captures_of(self.match)
        for effect in self.effects:
            missing = sorted(effect.placeholders() - available)
            if missing:
                raise ValueError(
                    f"The wording expects {', '.join(missing)}, which this rule's "
                    "conditions never capture. Name a capture from the pattern, or "
                    "say it in plain words."
                )
        return self

    def digest(self) -> str:
        """What makes this rule the same rule as another one.

        The type, what it is about and what it does. Not the name, the reason
        or the priority: renaming a rule does not make it a different rule,
        and neither does explaining it better.
        """
        payload = json.dumps(
            {
                "rule_type": self.rule_type.value,
                "capability_key": self.capability_key,
                "match": self.match.model_dump(mode="json"),
                "effects": [effect.model_dump(mode="json") for effect in self.effects],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def describes(self) -> list[str]:
        """The rule as sentences, generated from the rule itself."""
        lines = [f"Rule: {self.name}", "When:"]
        lines.extend(f"- {sentence}" for sentence in self.match.describes())
        lines.append("Then:")
        lines.extend(f"- {effect.describes()}" for effect in self.effects)
        lines.append(
            "- and nothing happens to the mailbox until you decide, the exact "
            "action is prepared, and you confirm it"
        )
        return lines


def captures_of(group: ConditionGroup) -> set[str]:
    """Every value this tree's patterns could give a message to say."""
    found: set[str] = set()
    for test in [*group.all, *group.any, *group.none]:
        if isinstance(test, ConditionGroup):
            found |= captures_of(test)
            continue
        found |= set(CAPTURE_NAMES.findall(test.value))
    return found


def check_group(effect: Effect, groups: Sequence[str]) -> None:
    """A rule may only put an email in a group the review actually has."""
    if effect.kind is not EffectKind.ASSIGN_EMAIL_GROUP:
        return
    if effect.group_key not in groups:
        raise RuleError(
            f"{effect.group_key!r} is not a group in this review. The groups are: "
            f"{', '.join(sorted(groups)) or 'none'}."
        )


def check_draft(
    draft: RuleDraft, capability: CapabilityConfig | None, groups: Sequence[str] = ()
) -> None:
    """Everything about a rule that its own shape cannot decide.

    Breadth, the groups this review has, and whether the capability it is
    written for is allowed to do what it recommends. Kept out of the model
    because each depends on something outside the rule: the field registry,
    and configuration that may have changed since the rule was written.
    """
    check_breadth(draft.match)

    for effect in draft.effects:
        check_group(effect, groups)

    if draft.capability_key is None:
        for effect in draft.effects:
            if effect.kind is EffectKind.RECOMMEND_ACTION:
                raise RuleError(
                    "A rule that recommends an action belongs to a capability, "
                    "because whether the action is allowed is the capability's to say."
                )
        return

    if capability is None:
        raise RuleError(f"{draft.capability_key!r} is not a capability in this configuration.")

    for effect in draft.effects:
        if effect.kind is not EffectKind.RECOMMEND_ACTION:
            continue
        if effect.action is None:
            continue
        if effect.action in {outcome.value for outcome in Recommendation}:
            continue
        action = read_action_kind(effect.action)
        if not capability.permits(action):
            raise RuleError(
                f"{capability.key!r} is not allowed to {action.value}, so a rule "
                "cannot recommend it."
            )
        destination = effect.params.get(DESTINATION_PARAM)
        if destination and destination not in capability.gmail.destinations:
            raise RuleError(
                f"{destination!r} is not one of {capability.key!r}'s folders. "
                f"It files mail in: {', '.join(capability.gmail.destinations) or 'none'}."
            )


def check_transition(current: RuleStatus, wanted: RuleStatus) -> None:
    """Whether a rule may move from where it is to where it is being put."""
    if wanted == current:
        raise RuleError(f"This rule is already {current}.")
    allowed = TRANSITIONS[current]
    if wanted not in allowed:
        raise RuleError(
            f"A {current} rule cannot become {wanted}. From {current} it may "
            f"become: {', '.join(sorted(allowed)) or 'nothing — this is final'}."
        )


def describe_rule_types() -> list[RuleTypeSpec]:
    """Every rule type, available or not, for a screen that has to list them."""
    return sorted(RULE_TYPES.values(), key=lambda spec: spec.rule_type.value)
