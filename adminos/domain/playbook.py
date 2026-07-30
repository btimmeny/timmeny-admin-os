"""The playbook: what a session works through, in what order, by configuration.

A playbook is Brian's operating process written down — email, then objectives,
then to-dos, then the calendar, and what each of those consists of. A session
is one execution of it against the state of the day. The two are separate on
purpose: the process should survive the morning, and the morning should not
quietly change the process.

Which is the other half of this. "Skip Legal today" is about today. "Always do
objectives before email" is a change to how every morning works, and it takes
the long way round: a proposal, read back as the exact effect, and then Brian's
word. Nothing said in passing becomes permanent by being said twice.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self, Sequence

import yaml

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from adminos.capabilities.config import LoadedCapabilities
from adminos.domain.activities import (
    ACTIVITIES,
    Availability,
    known_activity,
)
from adminos.logging import get_logger


logger = get_logger(__name__)

SCHEMA_VERSION = 1
"""The playbook layout this code understands.

Checked rather than assumed: a document written for a later layout is refused
with its version named, which is better than reading half of it correctly.
"""

DEFAULT_PLAYBOOK_ID = "brian-default"

ORDER_STEP = 10
"""The gap left between positions, so a thing can be moved between two others."""


class PlaybookError(RuntimeError):
    """Raised when a playbook document cannot be read at all."""


class ChangeRefused(RuntimeError):
    """Raised when a proposed change cannot be applied to the playbook.

    Distinct from an invalid result: this is a change that does not describe
    anything — moving an activity the playbook does not contain — rather than
    one whose result would be unworkable.
    """


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StepConfig(StrictModel):
    """One step of an activity, as the playbook names it.

    `capability_key` is the name for a step everywhere, including activities
    whose steps are not capabilities: "overdue to-dos" is addressed the same
    way as "admin email", so a caller reordering steps does not have to know
    which kind of activity it is looking at.
    """

    capability_key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    enabled: bool = True
    order: int


class ActivityConfig(StrictModel):
    activity_key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    enabled: bool = True
    order: int
    fresh_data_required: bool = True
    intro: str = ""
    steps: list[StepConfig] = []

    def enabled_steps(self) -> list[StepConfig]:
        return sorted(
            (step for step in self.steps if step.enabled), key=lambda step: step.order
        )


class SessionRules(StrictModel):
    """How a session opens, and whether it waits to be told to begin."""

    opening_mode: Literal["propose_then_start"] = "propose_then_start"
    auto_start_first_activity: bool = False
    show_activity_sequence: bool = True
    show_subprocess_before_activity: bool = True
    finish_with_summary: bool = True


class ColumnFilterConfig(StrictModel):
    """One column of a board, and the labels on it that qualify an item.

    The column is named by its Monday id rather than its title, because a
    title is renamed by whoever owns the board and an id is not. The labels
    are matched on exact text: a filter that falls back to something near
    enough is a filter that reviews work Brian never put in front of himself.
    """

    column_id: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    labels: list[str] = Field(min_length=1)


class MondayScopeConfig(StrictModel):
    """Which Monday items a review is of, in ids and labels, or not at all.

    Every part of this is named here or the review does not look. There is no
    default board, no default column and no default label, because the failure
    mode of a wrong guess is reviewing the wrong work — or, worse, a filter
    that matches nothing and returns a thousand-item board as if it were the
    day's list.
    """

    board_id: str = Field(pattern=r"^\d+$")
    match: Literal["any"] = "any"
    """How the filters combine. Only "any" today: qualifying on either column."""

    filters: list[ColumnFilterConfig] = Field(min_length=1)

    def column_ids(self) -> list[str]:
        return [column.column_id for column in self.filters]


class SourcesConfig(StrictModel):
    """The systems a session reads besides Gmail.

    Absent means unconfigured, and unconfigured is reported as unavailable
    rather than filled in: a Monday review of a board nobody named is a review
    of somebody's guess.
    """

    monday: MondayScopeConfig | None = None


class PlaybookDocument(StrictModel):
    schema_version: int = SCHEMA_VERSION
    playbook_id: str = DEFAULT_PLAYBOOK_ID
    name: str
    session: SessionRules = SessionRules()
    sources: SourcesConfig = SourcesConfig()
    activities: list[ActivityConfig] = Field(min_length=1)

    def ordered(self) -> list[ActivityConfig]:
        return sorted(self.activities, key=lambda activity: activity.order)

    def enabled(self) -> list[ActivityConfig]:
        return [activity for activity in self.ordered() if activity.enabled]

    def find(self, activity_key: str) -> ActivityConfig | None:
        for activity in self.activities:
            if activity.activity_key == activity_key:
                return activity
        return None


class ValidationCode(StrEnum):
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNKNOWN_ACTIVITY = "UNKNOWN_ACTIVITY"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
    UNKNOWN_STEP = "UNKNOWN_STEP"
    DUPLICATE_ACTIVITY = "DUPLICATE_ACTIVITY"
    DUPLICATE_STEP = "DUPLICATE_STEP"
    AMBIGUOUS_ORDER = "AMBIGUOUS_ORDER"
    NO_ENABLED_STEPS = "NO_ENABLED_STEPS"
    NO_ENABLED_ACTIVITIES = "NO_ENABLED_ACTIVITIES"
    ACTIVITY_NOT_BUILT = "ACTIVITY_NOT_BUILT"
    NO_CLOSEOUT = "NO_CLOSEOUT"


@dataclass(frozen=True)
class ValidationMessage:
    path: str
    code: ValidationCode
    message: str


@dataclass(frozen=True)
class ValidationReport:
    """Whether this playbook can be worked, and what is wrong where it cannot.

    Errors and warnings are different sentences. An error is a playbook that
    cannot be run as written and never becomes active. A warning is a playbook
    that runs while saying something true about itself — that it contains an
    activity Brian works through and Admin OS cannot yet.
    """

    errors: tuple[ValidationMessage, ...] = ()
    warnings: tuple[ValidationMessage, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors


def validate_playbook(
    document: PlaybookDocument, loaded: LoadedCapabilities
) -> ValidationReport:
    """Check every activity and step against what is actually implemented.

    The registry is the authority, not the file. A step naming a capability
    nobody configured would otherwise be a group silently missing from the
    review, and the whole reason a session states its plan first is so that
    what is about to happen is what happens.
    """
    errors: list[ValidationMessage] = []
    warnings: list[ValidationMessage] = []
    capabilities = {capability.key for capability in loaded.enabled()}

    if document.schema_version != SCHEMA_VERSION:
        errors.append(
            ValidationMessage(
                path="schema_version",
                code=ValidationCode.UNSUPPORTED_SCHEMA,
                message=(
                    f"Playbook schema version {document.schema_version} is not "
                    f"supported; this service reads version {SCHEMA_VERSION}."
                ),
            )
        )

    check_unique(
        [activity.activity_key for activity in document.activities],
        path="activities",
        code=ValidationCode.DUPLICATE_ACTIVITY,
        noun="activity",
        errors=errors,
    )
    check_order(
        [activity.order for activity in document.activities], path="activities", errors=errors
    )

    for activity in document.activities:
        path = f"activities.{activity.activity_key}"
        kind = known_activity(activity.activity_key)
        if kind is None:
            errors.append(
                ValidationMessage(
                    path=f"{path}.activity_key",
                    code=ValidationCode.UNKNOWN_ACTIVITY,
                    message=(
                        f"Activity {activity.activity_key!r} is not implemented. "
                        f"Known activities: {', '.join(a.key for a in ACTIVITIES)}."
                    ),
                )
            )
            continue

        if activity.enabled and kind.availability is Availability.PLANNED:
            warnings.append(
                ValidationMessage(
                    path=path,
                    code=ValidationCode.ACTIVITY_NOT_BUILT,
                    message=(
                        f"{kind.label} is in the playbook and is not built yet, so a "
                        "session will name it and move past it rather than work it."
                    ),
                )
            )

        check_unique(
            [step.capability_key for step in activity.steps],
            path=f"{path}.steps",
            code=ValidationCode.DUPLICATE_STEP,
            noun="step",
            errors=errors,
        )
        check_order([step.order for step in activity.steps], path=f"{path}.steps", errors=errors)

        for index, step in enumerate(activity.steps):
            step_path = f"{path}.steps[{index}].capability_key"
            if kind.steps_are_capabilities:
                if step.capability_key not in capabilities:
                    errors.append(
                        ValidationMessage(
                            path=step_path,
                            code=ValidationCode.UNKNOWN_CAPABILITY,
                            message=(
                                f"Capability key {step.capability_key!r} is not "
                                f"configured. Configured: "
                                f"{', '.join(sorted(capabilities)) or 'none'}."
                            ),
                        )
                    )
            elif not kind.knows_step(step.capability_key):
                errors.append(
                    ValidationMessage(
                        path=step_path,
                        code=ValidationCode.UNKNOWN_STEP,
                        message=(
                            f"{kind.label} has no step named {step.capability_key!r}. "
                            f"Its steps: {', '.join(s.key for s in kind.steps)}."
                        ),
                    )
                )

        if activity.enabled and kind.built() and not activity.enabled_steps():
            errors.append(
                ValidationMessage(
                    path=f"{path}.steps",
                    code=ValidationCode.NO_ENABLED_STEPS,
                    message=(
                        f"{activity.label} is enabled with every step turned off, so "
                        "the session would announce it and have nothing to do."
                    ),
                )
            )

    if not document.enabled():
        errors.append(
            ValidationMessage(
                path="activities",
                code=ValidationCode.NO_ENABLED_ACTIVITIES,
                message="A playbook with no enabled activity is not a session.",
            )
        )

    if document.session.finish_with_summary and not any(
        activity.activity_key == "session_closeout" for activity in document.enabled()
    ):
        warnings.append(
            ValidationMessage(
                path="session.finish_with_summary",
                code=ValidationCode.NO_CLOSEOUT,
                message=(
                    "The playbook says to finish with a summary and has no closeout "
                    "activity enabled, so the session will end without one."
                ),
            )
        )

    return ValidationReport(errors=tuple(errors), warnings=tuple(warnings))


def check_unique(
    keys: Sequence[str],
    path: str,
    code: ValidationCode,
    noun: str,
    errors: list[ValidationMessage],
) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            errors.append(
                ValidationMessage(
                    path=path,
                    code=code,
                    message=f"The {noun} {key!r} appears twice; which one is meant?",
                )
            )
        seen.add(key)


def check_order(orders: Sequence[int], path: str, errors: list[ValidationMessage]) -> None:
    """Two things at the same position have no order, only an accident of storage."""
    if len(set(orders)) != len(orders):
        errors.append(
            ValidationMessage(
                path=path,
                code=ValidationCode.AMBIGUOUS_ORDER,
                message=(
                    "Two entries share a position, so the order they are worked in "
                    "would depend on how they happen to be stored."
                ),
            )
        )


class EnableActivity(StrictModel):
    operation: Literal["enable_activity", "disable_activity"]
    activity_key: str


class MoveActivity(StrictModel):
    operation: Literal["move_activity"]
    activity_key: str
    before_activity_key: str | None = None
    after_activity_key: str | None = None

    @model_validator(mode="after")
    def require_one_anchor(self) -> Self:
        if (self.before_activity_key is None) == (self.after_activity_key is None):
            raise ValueError(
                "A move says either before or after, and exactly one of them; "
                "otherwise it does not say where."
            )
        return self


class EnableStep(StrictModel):
    operation: Literal["enable_step", "disable_step"]
    activity_key: str
    capability_key: str


class MoveStep(StrictModel):
    operation: Literal["move_step"]
    activity_key: str
    capability_key: str
    before_capability_key: str | None = None
    after_capability_key: str | None = None

    @model_validator(mode="after")
    def require_one_anchor(self) -> Self:
        if (self.before_capability_key is None) == (self.after_capability_key is None):
            raise ValueError(
                "A move says either before or after, and exactly one of them; "
                "otherwise it does not say where."
            )
        return self


class AddStep(StrictModel):
    operation: Literal["add_step"]
    activity_key: str
    capability_key: str
    label: str | None = None


class RemoveStep(StrictModel):
    operation: Literal["remove_step"]
    activity_key: str
    capability_key: str


class UpdateIntro(StrictModel):
    operation: Literal["update_intro"]
    activity_key: str
    intro: str


PlaybookChange = (
    EnableActivity | MoveActivity | EnableStep | MoveStep | AddStep | RemoveStep | UpdateIntro
)


CHANGE_READER: TypeAdapter[PlaybookChange] = TypeAdapter(PlaybookChange)


def read_change(value: dict[str, object]) -> PlaybookChange:
    """Read one change from the shape it arrives in.

    The operation names itself, and anything the named operation does not take
    is refused rather than ignored: a change with a misspelt field is a change
    that would not do what it says.
    """
    try:
        return CHANGE_READER.validate_python(value)
    except ValidationError as exc:
        raise ChangeRefused(f"The change cannot be read: {exc}") from exc


@dataclass(frozen=True)
class ChangedPlaybook:
    """A playbook with changes applied, and the sentences describing them.

    The sentences are the point of the return value. A proposal is confirmed
    by Brian reading back what it does, so the effect is written when the
    change is applied rather than reconstructed afterwards from two documents.
    """

    document: PlaybookDocument
    summary: tuple[str, ...]


def apply_changes(
    document: PlaybookDocument,
    changes: Sequence[PlaybookChange],
    loaded: LoadedCapabilities,
) -> ChangedPlaybook:
    """Work the changes through the playbook, in the order they were given."""
    if not changes:
        raise ChangeRefused("A proposal with no changes changes nothing.")

    working = document.model_dump()
    summary: list[str] = []
    for change in changes:
        working, said = apply_change(working, change, loaded)
        summary.append(said)

    try:
        changed = PlaybookDocument.model_validate(working)
    except ValidationError as exc:
        raise ChangeRefused(f"The change leaves a playbook that cannot be read: {exc}") from exc
    return ChangedPlaybook(document=renumber(changed), summary=tuple(summary))


def apply_change(
    working: dict[str, object], change: PlaybookChange, loaded: LoadedCapabilities
) -> tuple[dict[str, object], str]:
    document = PlaybookDocument.model_validate(working)
    activities = [activity.model_copy(deep=True) for activity in document.ordered()]
    changed, said = rewrite(activities, change, loaded)
    return (
        renumber(document.model_copy(update={"activities": changed})).model_dump(),
        said,
    )


def rewrite(
    activities: list[ActivityConfig], change: PlaybookChange, loaded: LoadedCapabilities
) -> tuple[list[ActivityConfig], str]:
    if isinstance(change, EnableActivity):
        return toggle_activity(activities, change)
    if isinstance(change, MoveActivity):
        return move_activity(activities, change)
    if isinstance(change, EnableStep):
        return toggle_step(activities, change)
    if isinstance(change, MoveStep):
        return move_step(activities, change)
    if isinstance(change, AddStep):
        return add_step(activities, change, loaded)
    if isinstance(change, RemoveStep):
        return remove_step(activities, change)
    return update_intro(activities, change)


def find(activities: list[ActivityConfig], key: str) -> ActivityConfig:
    for activity in activities:
        if activity.activity_key == key:
            return activity
    raise ChangeRefused(
        f"The playbook has no activity {key!r}. It has "
        f"{', '.join(activity.activity_key for activity in activities)}."
    )


def toggle_activity(
    activities: list[ActivityConfig], change: EnableActivity
) -> tuple[list[ActivityConfig], str]:
    """Turn an activity on or off, adding it from the registry if it is absent.

    "Add a calendar review" and "turn the calendar review back on" are the
    same request in different words, so enabling an activity the playbook has
    never contained brings it in with its registered steps.
    """
    wanted = change.operation == "enable_activity"
    existing = next(
        (activity for activity in activities if activity.activity_key == change.activity_key),
        None,
    )

    if existing is None:
        if not wanted:
            raise ChangeRefused(
                f"The playbook has no activity {change.activity_key!r} to disable."
            )
        kind = known_activity(change.activity_key)
        if kind is None:
            raise ChangeRefused(
                f"{change.activity_key!r} is not an activity this service knows."
            )
        added = ActivityConfig(
            activity_key=kind.key,
            label=kind.label,
            enabled=True,
            order=(activities[-1].order if activities else 0) + ORDER_STEP,
            fresh_data_required=kind.built(),
            intro="",
            steps=[
                StepConfig(capability_key=step.key, label=step.label, order=index * ORDER_STEP)
                for index, step in enumerate(kind.steps, start=1)
            ],
        )
        return activities + [added], f"{kind.label} is added to the session."

    index = activities.index(existing)
    activities[index] = existing.model_copy(update={"enabled": wanted})
    verb = "reviewed in every session" if wanted else "not reviewed"
    return activities, f"{existing.label} is {verb}."


def move_activity(
    activities: list[ActivityConfig], change: MoveActivity
) -> tuple[list[ActivityConfig], str]:
    moving = find(activities, change.activity_key)
    anchor_key = change.before_activity_key or change.after_activity_key
    if anchor_key == change.activity_key:
        raise ChangeRefused("An activity cannot be moved before or after itself.")
    anchor = find(activities, anchor_key or "")

    remaining = [activity for activity in activities if activity is not moving]
    at = remaining.index(anchor) + (0 if change.before_activity_key else 1)
    remaining.insert(at, moving)
    where = "before" if change.before_activity_key else "after"
    return remaining, f"{moving.label} moves {where} {anchor.label}."


def toggle_step(
    activities: list[ActivityConfig], change: EnableStep
) -> tuple[list[ActivityConfig], str]:
    activity = find(activities, change.activity_key)
    step = find_step(activity, change.capability_key)
    wanted = change.operation == "enable_step"
    index = activities.index(activity)
    steps = [
        step.model_copy(update={"enabled": wanted}) if existing is step else existing
        for existing in activity.steps
    ]
    activities[index] = activity.model_copy(update={"steps": steps})
    verb = "reviewed" if wanted else "not reviewed"
    return activities, f"{step.label} is {verb} in {activity.label}."


def move_step(
    activities: list[ActivityConfig], change: MoveStep
) -> tuple[list[ActivityConfig], str]:
    activity = find(activities, change.activity_key)
    moving = find_step(activity, change.capability_key)
    anchor_key = change.before_capability_key or change.after_capability_key
    if anchor_key == change.capability_key:
        raise ChangeRefused("A step cannot be moved before or after itself.")
    anchor = find_step(activity, anchor_key or "")

    ordered = sorted(activity.steps, key=lambda step: step.order)
    remaining = [step for step in ordered if step is not moving]
    at = remaining.index(anchor) + (0 if change.before_capability_key else 1)
    remaining.insert(at, moving)
    index = activities.index(activity)
    activities[index] = activity.model_copy(update={"steps": remaining})
    where = "before" if change.before_capability_key else "after"
    return activities, f"{moving.label} moves {where} {anchor.label} in {activity.label}."


def add_step(
    activities: list[ActivityConfig], change: AddStep, loaded: LoadedCapabilities
) -> tuple[list[ActivityConfig], str]:
    activity = find(activities, change.activity_key)
    if any(step.capability_key == change.capability_key for step in activity.steps):
        raise ChangeRefused(
            f"{activity.label} already has a step {change.capability_key!r}; enabling "
            "it is `enable_step`."
        )
    label = change.label or step_label(change.activity_key, change.capability_key, loaded)
    index = activities.index(activity)
    highest = max((step.order for step in activity.steps), default=0)
    steps = list(activity.steps) + [
        StepConfig(capability_key=change.capability_key, label=label, order=highest + ORDER_STEP)
    ]
    activities[index] = activity.model_copy(update={"steps": steps})
    return activities, f"{label} is added to {activity.label}."


def remove_step(
    activities: list[ActivityConfig], change: RemoveStep
) -> tuple[list[ActivityConfig], str]:
    activity = find(activities, change.activity_key)
    step = find_step(activity, change.capability_key)
    index = activities.index(activity)
    steps = [existing for existing in activity.steps if existing is not step]
    activities[index] = activity.model_copy(update={"steps": steps})
    return activities, f"{step.label} is removed from {activity.label}."


def update_intro(
    activities: list[ActivityConfig], change: UpdateIntro
) -> tuple[list[ActivityConfig], str]:
    activity = find(activities, change.activity_key)
    index = activities.index(activity)
    activities[index] = activity.model_copy(update={"intro": change.intro})
    return activities, f"{activity.label} is introduced with new wording."


def find_step(activity: ActivityConfig, capability_key: str) -> StepConfig:
    for step in activity.steps:
        if step.capability_key == capability_key:
            return step
    raise ChangeRefused(
        f"{activity.label} has no step {capability_key!r}. It has "
        f"{', '.join(step.capability_key for step in activity.steps) or 'none'}."
    )


def step_label(activity_key: str, capability_key: str, loaded: LoadedCapabilities) -> str:
    """What a step is called, asking whatever owns the name."""
    kind = known_activity(activity_key)
    if kind is not None:
        for step in kind.steps:
            if step.key == capability_key:
                return step.label
    for capability in loaded.capabilities:
        if capability.key == capability_key:
            return capability.name
    return capability_key


def renumber(document: PlaybookDocument) -> PlaybookDocument:
    """Space the positions out again, keeping the order they are already in.

    Positions are storage rather than meaning: what matters is the sequence,
    and rewriting them in tens after every change keeps room to insert without
    two things ever sharing a place.
    """
    activities = [
        activity.model_copy(
            update={
                "order": (index + 1) * ORDER_STEP,
                "steps": [
                    step.model_copy(update={"order": (position + 1) * ORDER_STEP})
                    for position, step in enumerate(
                        sorted(activity.steps, key=lambda step: step.order)
                    )
                ],
            }
        )
        for index, activity in enumerate(document.activities)
    ]
    return document.model_copy(update={"activities": activities})


def parse_playbook(raw: bytes) -> PlaybookDocument:
    try:
        loaded_document: object = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise PlaybookError(f"The playbook is not valid YAML: {exc}") from exc
    if not isinstance(loaded_document, dict):
        raise PlaybookError("A playbook must be a mapping.")
    return read_playbook(loaded_document)


def read_playbook(document: dict[str, object]) -> PlaybookDocument:
    try:
        return PlaybookDocument.model_validate(document)
    except ValidationError as exc:
        raise PlaybookError(f"The playbook cannot be read: {exc}") from exc
