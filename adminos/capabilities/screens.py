"""Presentation contracts: what a review looks like, owned by Admin OS.

A screen is configuration, not code and not prompt text. It names the columns,
their order, how each value is formatted, how rows are sorted, and which
decisions the reader may take. The GPT renders what it is given; it does not
choose a layout, invent a column, or decide what a confidence of 0.85 should
look like.

Every column names a `ColumnSource`, and every source is computed by this
service. A contract therefore cannot ask for data that does not exist, and
adding a column to a screen is an edit to one file rather than a change to a
prompt nobody can review.
"""

from enum import StrEnum
from string import Formatter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from adminos.domain.decisions import DecisionKind


class ColumnSource(StrEnum):
    """Every value a column may show. Each is derived server-side."""

    INDEX = "index"
    GROUP = "group"
    WHAT_IT_IS = "what_it_is"
    KEY_FACTS = "key_facts"
    RECOMMENDED_ACTION = "recommended_action"
    WHY = "why"
    CONFIDENCE = "confidence"
    DECISION = "decision"
    SUBJECT = "subject"
    PARTICIPANTS = "participants"
    RECEIVED = "received"
    STATE = "state"


class ValueType(StrEnum):
    """What kind of value a source produces, which decides how it may be formatted."""

    TEXT = "text"
    NUMBER = "number"
    FRACTION = "fraction"
    TIMESTAMP = "timestamp"
    LIST = "list"


class ColumnFormat(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    PERCENT = "percent"
    DATE = "date"
    RELATIVE_DATE = "relative_date"
    JOINED = "joined"


SOURCE_TYPES: dict[ColumnSource, ValueType] = {
    ColumnSource.INDEX: ValueType.NUMBER,
    ColumnSource.GROUP: ValueType.TEXT,
    ColumnSource.WHAT_IT_IS: ValueType.TEXT,
    ColumnSource.KEY_FACTS: ValueType.TEXT,
    ColumnSource.RECOMMENDED_ACTION: ValueType.TEXT,
    ColumnSource.WHY: ValueType.TEXT,
    ColumnSource.CONFIDENCE: ValueType.FRACTION,
    ColumnSource.DECISION: ValueType.TEXT,
    ColumnSource.SUBJECT: ValueType.TEXT,
    ColumnSource.PARTICIPANTS: ValueType.LIST,
    ColumnSource.RECEIVED: ValueType.TIMESTAMP,
    ColumnSource.STATE: ValueType.TEXT,
}

FORMATS_BY_TYPE: dict[ValueType, set[ColumnFormat]] = {
    ValueType.TEXT: {ColumnFormat.TEXT},
    ValueType.NUMBER: {ColumnFormat.NUMBER},
    ValueType.FRACTION: {ColumnFormat.PERCENT, ColumnFormat.NUMBER},
    ValueType.TIMESTAMP: {ColumnFormat.DATE, ColumnFormat.RELATIVE_DATE},
    ValueType.LIST: {ColumnFormat.JOINED},
}

DEFAULT_FORMATS: dict[ValueType, ColumnFormat] = {
    ValueType.TEXT: ColumnFormat.TEXT,
    ValueType.NUMBER: ColumnFormat.NUMBER,
    ValueType.FRACTION: ColumnFormat.PERCENT,
    ValueType.TIMESTAMP: ColumnFormat.RELATIVE_DATE,
    ValueType.LIST: ColumnFormat.JOINED,
}

SORTABLE_SOURCES = {
    ColumnSource.RECEIVED,
    ColumnSource.CONFIDENCE,
    ColumnSource.SUBJECT,
    ColumnSource.STATE,
}

FOOTER_FIELDS = {
    "capability",
    "screen_id",
    "total",
    "pending",
    "decided",
    "approved",
    "review_date",
}
"""The only substitutions a footer may use. An unknown one fails at load."""


class ScreenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScreenColumn(ScreenModel):
    label: str
    source: ColumnSource
    format: ColumnFormat | None = None
    align: Literal["left", "right"] = "left"
    max_chars: int | None = Field(default=None, ge=8)
    empty_text: str = "—"

    @model_validator(mode="after")
    def check_format_suits_the_source(self) -> Self:
        if self.format is None:
            return self
        allowed = FORMATS_BY_TYPE[SOURCE_TYPES[self.source]]
        if self.format not in allowed:
            names = ", ".join(sorted(value.value for value in allowed))
            raise ValueError(
                f"Column {self.label!r} formats {self.source.value!r} as "
                f"{self.format.value!r}; it can only be {names}."
            )
        return self

    def resolved_format(self) -> ColumnFormat:
        return self.format or DEFAULT_FORMATS[SOURCE_TYPES[self.source]]


class ScreenSort(ScreenModel):
    source: ColumnSource
    direction: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def check_sortable(self) -> Self:
        if self.source not in SORTABLE_SOURCES:
            names = ", ".join(sorted(source.value for source in SORTABLE_SOURCES))
            raise ValueError(f"Rows cannot be ordered by {self.source.value!r}; only by {names}.")
        return self


class ScreenAction(ScreenModel):
    """One thing the reader may do, and the decision it sends back.

    The screen declares intent; the endpoint, method, and body are filled in by
    the service when the screen is rendered, so a contract can never point at a
    route that does not exist.
    """

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    decision: DecisionKind
    action: str | None = None
    scope: Literal["item", "group"] = "item"

    @model_validator(mode="after")
    def check_action_matches_decision(self) -> Self:
        if self.decision is DecisionKind.OVERRIDE and self.action is None:
            raise ValueError(f"Action {self.id!r} overrides without naming an action to take.")
        if self.decision is not DecisionKind.OVERRIDE and self.action is not None:
            raise ValueError(
                f"Action {self.id!r} names {self.action!r}, but only an override chooses "
                "an action; approving takes the recommendation."
            )
        return self


class ScreenConfig(ScreenModel):
    """A versioned presentation contract, referenced by capabilities by id."""

    id: str = Field(pattern=r"^[a-z0-9-]+-v\d+$")
    kind: Literal["table"] = "table"
    title: str
    columns: list[ScreenColumn] = Field(min_length=1)
    sort: list[ScreenSort] = []
    actions: list[ScreenAction] = Field(min_length=1)
    action_labels: dict[str, str] = {}
    footer: str = ""
    empty_text: str = "Nothing here needs you."

    @model_validator(mode="after")
    def check_screen_is_coherent(self) -> Self:
        sources = [column.source for column in self.columns]
        if len(set(sources)) != len(sources):
            raise ValueError(f"Screen {self.id!r} shows the same value in two columns.")

        ids = [action.id for action in self.actions]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Screen {self.id!r} declares the same action twice.")

        for field in Formatter().parse(self.footer):
            name = field[1]
            if name is not None and name not in FOOTER_FIELDS:
                known = ", ".join(sorted(FOOTER_FIELDS))
                raise ValueError(
                    f"Screen {self.id!r} has a footer using {{{name}}}, which is not a "
                    f"known value. Use one of: {known}."
                )
        return self
