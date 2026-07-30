"""What a session can be made of, and what it cannot.

A playbook says what Brian and Admin OS work through and in what order. This
is the list of things it is allowed to name. Configuration that names anything
else is refused rather than skipped, because a playbook silently missing a step
is a session quietly not doing something Brian asked for.

Two kinds of thing are named here, and the difference is the point:

- an activity that is `built` has code behind it and can be worked;
- an activity that is `planned` is a real part of Brian's operating process
  with nothing behind it yet. It may sit in the playbook, and a session says
  so plainly rather than pretending to review a calendar nobody has read.

The third case — a key nobody has heard of — is an error, and the revision
carrying it never becomes active.
"""

from dataclasses import dataclass
from enum import StrEnum


class Availability(StrEnum):
    """Whether an activity can actually be worked, or only configured."""

    BUILT = "built"
    PLANNED = "planned"


class DataSource(StrEnum):
    """Where an activity's live state comes from, where it has one."""

    GMAIL = "gmail"
    MONDAY = "monday"
    CALENDAR = "calendar"
    NONE = "none"
    """The closeout reads no system: it reports the session it is closing."""


@dataclass(frozen=True)
class StepKind:
    """One step of an activity, by the name configuration uses for it."""

    key: str
    label: str


@dataclass(frozen=True)
class ActivityKind:
    """One activity a playbook may include, and what it is made of."""

    key: str
    label: str
    availability: Availability
    source: DataSource
    steps: tuple[StepKind, ...] = ()
    """The steps this activity can be configured with.

    Empty for the email review alone, whose steps are the configured
    capabilities: adding a capability to `capabilities.yaml` is what adds a
    step there, and a second list of the same keys would be a second place to
    forget to change.
    """

    steps_are_capabilities: bool = False

    def built(self) -> bool:
        return self.availability is Availability.BUILT

    def knows_step(self, key: str) -> bool:
        return any(step.key == key for step in self.steps)


EMAIL_REVIEW = ActivityKind(
    key="email_review",
    label="Email",
    availability=Availability.BUILT,
    source=DataSource.GMAIL,
    steps_are_capabilities=True,
)

OBJECTIVES_REVIEW = ActivityKind(
    key="objectives_review",
    label="Objectives",
    availability=Availability.PLANNED,
    source=DataSource.MONDAY,
    steps=(
        StepKind("annual_objectives", "Annual objectives"),
        StepKind("quarterly_objectives", "Quarterly objectives"),
        StepKind("current_priorities", "Current priorities"),
        StepKind("at_risk_objectives", "At-risk objectives"),
        StepKind("objectives_without_next_actions", "Objectives without next actions"),
        StepKind("recently_completed_objectives", "Recently completed objectives"),
    ),
)

TODO_REVIEW = ActivityKind(
    key="todo_review",
    label="To-Dos",
    availability=Availability.PLANNED,
    source=DataSource.MONDAY,
    steps=(
        StepKind("overall_todos", "Overall to-dos"),
        StepKind("overdue_todos", "Overdue"),
        StepKind("due_today", "Due today"),
        StepKind("blocked_todos", "Blocked"),
        StepKind("waiting_todos", "Waiting on others"),
        StepKind("upcoming_todos", "Upcoming"),
        StepKind("delegated_todos", "Delegated"),
        StepKind("completed_since_last_review", "Completed since last review"),
    ),
)

CALENDAR_REVIEW = ActivityKind(
    key="calendar_review",
    label="Calendar",
    availability=Availability.PLANNED,
    source=DataSource.CALENDAR,
    steps=(
        StepKind("todays_calendar", "Today"),
        StepKind("calendar_conflicts", "Conflicts"),
        StepKind("meeting_preparation", "Preparation needed"),
        StepKind("focus_time", "Focus time"),
        StepKind("movable_meetings", "Meetings that can be moved"),
        StepKind("upcoming_deadlines", "Upcoming deadlines"),
    ),
)

FOLLOW_UP_REVIEW = ActivityKind(
    key="follow_up_review",
    label="Follow-ups",
    availability=Availability.PLANNED,
    source=DataSource.GMAIL,
    steps=(
        StepKind("awaiting_reply", "Awaiting a reply"),
        StepKind("promised_by_me", "Promised by me"),
        StepKind("overdue_follow_ups", "Overdue follow-ups"),
    ),
)

SESSION_CLOSEOUT = ActivityKind(
    key="session_closeout",
    label="Closeout",
    availability=Availability.BUILT,
    source=DataSource.NONE,
    steps=(StepKind("session_summary", "Session summary"),),
)


ACTIVITIES: tuple[ActivityKind, ...] = (
    EMAIL_REVIEW,
    OBJECTIVES_REVIEW,
    TODO_REVIEW,
    CALENDAR_REVIEW,
    FOLLOW_UP_REVIEW,
    SESSION_CLOSEOUT,
)


def known_activity(key: str) -> ActivityKind | None:
    for activity in ACTIVITIES:
        if activity.key == key:
            return activity
    return None


def activity_keys() -> list[str]:
    return [activity.key for activity in ACTIVITIES]
