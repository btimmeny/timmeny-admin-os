"""Configuration comes off the board as the board holds it, or not at all.

The failures worth guarding against here are all quiet ones. A renamed column
returns items with no instructions. A status filter that did not apply returns
drafts as though Brian had agreed to them. An unknown type label returns an
empty list that reads exactly like "nothing is configured". Each of those is a
review run on rules nobody agreed to, so each is a refusal that names what the
board actually has.
"""

import asyncio
import json

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import pytest

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import main
from adminos.adapters.monday import MondayColumn, MondayError, MondayItem
from adminos.capabilities.config import clear_cache
from adminos.db import engine as engine_module
from adminos.domain.configuration import (
    EMAIL_CONFIGURATION,
    ConfigurationUnavailable,
    read_configuration,
)
from adminos.mcp import tools as tools_module
from tests.test_adminos_review_api import API_KEY, AUTH, CONFIG_PATH, REPOSITORY_ROOT


BOARD_ID = "18424609108"
BOARD_NAME = "Admin OS Config"
GROUP = "Configurations"

STATUS_COLUMN = "color_mm5stshs"
TYPE_COLUMN = "dropdown_mm5sjzfd"
TRIGGER_COLUMN = "long_text_mm5s26rf"
INSTRUCTIONS_COLUMN = "long_text_mm5s61kk"
CONTEXT_COLUMN = "long_text_mm5scqe7"
EXPECTED_COLUMN = "long_text_mm5sc0ay"
ORDER_COLUMN = "numeric_mm5s1ke"
GUARDRAILS_COLUMN = "long_text_mm5sfkev"

STATUS_SETTINGS = json.dumps({"labels": {"0": "Draft", "1": "Active", "2": "Inactive"}})
TYPE_SETTINGS = json.dumps(
    {
        "labels": [
            {"id": 1, "name": "Process"},
            {"id": 2, "name": "Email Rule"},
            {"id": 3, "name": "To-Do Rule"},
            {"id": 4, "name": "Reference Data"},
        ]
    }
)

COLUMNS = [
    MondayColumn("name", "Name", "name", None),
    MondayColumn(TYPE_COLUMN, "Configuration Type", "dropdown", TYPE_SETTINGS),
    MondayColumn(STATUS_COLUMN, "Status", "status", STATUS_SETTINGS),
    MondayColumn(TRIGGER_COLUMN, "Trigger / Match", "long_text", None),
    MondayColumn(INSTRUCTIONS_COLUMN, "Instructions / Logic", "long_text", None),
    MondayColumn(CONTEXT_COLUMN, "Context Needed", "long_text", None),
    MondayColumn(EXPECTED_COLUMN, "Expected Output", "long_text", None),
    MondayColumn(ORDER_COLUMN, "Order", "numbers", None),
    MondayColumn(GUARDRAILS_COLUMN, "Notes / Guardrails", "long_text", None),
]


def item(
    item_id: str,
    name: str,
    kind: str,
    status: str = "Active",
    order: str | None = "10",
) -> MondayItem:
    return MondayItem(
        item_id=item_id,
        name=name,
        group=GROUP,
        status=None,
        admin_os_id=None,
        action_date=None,
        values={
            STATUS_COLUMN: status,
            TYPE_COLUMN: kind,
            TRIGGER_COLUMN: f"{name} triggers",
            INSTRUCTIONS_COLUMN: f"{name} instructions",
            CONTEXT_COLUMN: f"{name} context",
            EXPECTED_COLUMN: f"{name} output",
            ORDER_COLUMN: order,
            GUARDRAILS_COLUMN: f"{name} guardrails",
        },
    )


PROCESS = item("12683708277", "Email review process", "Process")
UBER = item("12683595846", "Uber receipt interpretation", "Email Rule")


class FakeBoard:
    """A Monday that answers about the configuration board, and writes nothing.

    It applies the status rule itself, which is the point: a test that filtered
    nothing would pass whatever the caller asked for.
    """

    def __init__(
        self,
        items: list[MondayItem],
        columns: list[MondayColumn] | None = None,
        obeys_rules: bool = True,
        fails: MondayError | None = None,
    ) -> None:
        self.items = items
        self.columns = columns if columns is not None else COLUMNS
        self.obeys_rules = obeys_rules
        self.fails = fails
        self.reads: list[dict[str, Any]] = []

    async def read_board(self, board_id: str) -> tuple[str, list[MondayColumn]]:
        if self.fails is not None:
            raise self.fails
        return BOARD_NAME, self.columns

    async def list_items_matching(
        self,
        board_id: str,
        rules: list[dict[str, Any]],
        column_ids: list[str],
        operator: str = "or",
        limit: int = 500,
    ) -> list[MondayItem]:
        self.reads.append(
            {"board_id": board_id, "rules": rules, "column_ids": column_ids}
        )
        if not self.obeys_rules:
            return self.items
        wanted = {
            index_of(rule["compare_value"]) for rule in rules if rule["column_id"] == STATUS_COLUMN
        }
        return [
            one
            for one in self.items
            if label_index(one.values.get(STATUS_COLUMN)) in wanted
        ]


LABEL_INDEXES = {"Draft": 0, "Active": 1, "Inactive": 2}


def label_index(text: str | None) -> int | None:
    return LABEL_INDEXES.get(text or "")


def index_of(compare: Any) -> int:
    return int(compare[0])


def read(board: FakeBoard, configuration_type: str = "email") -> Any:
    return asyncio.run(read_configuration(board, BOARD_ID, configuration_type))


def test_active_items_come_back_split_by_their_configuration_type() -> None:
    configuration = read(FakeBoard([PROCESS, UBER]))

    assert [entry.name for entry in configuration.processes] == ["Email review process"]
    assert [entry.name for entry in configuration.email_configurations] == [
        "Uber receipt interpretation"
    ]
    assert configuration.board_id == BOARD_ID
    assert configuration.board_name == BOARD_NAME


def test_an_entry_carries_every_column_the_board_holds_for_it() -> None:
    configuration = read(FakeBoard([UBER]))
    entry = configuration.email_configurations[0]

    assert entry.item_id == "12683595846"
    assert entry.key == "uber_receipt_interpretation"
    assert entry.group_name == GROUP
    assert entry.configuration_type == "Email Rule"
    assert entry.trigger == "Uber receipt interpretation triggers"
    assert entry.instructions == "Uber receipt interpretation instructions"
    assert entry.context_needed == "Uber receipt interpretation context"
    assert entry.expected_output == "Uber receipt interpretation output"
    assert entry.guardrails == "Uber receipt interpretation guardrails"
    assert entry.order == 10


def test_draft_and_inactive_items_are_left_on_the_board() -> None:
    """Configuration Brian has not switched on is not configuration."""
    board = FakeBoard(
        [
            UBER,
            item("2", "Draft rule", "Email Rule", status="Draft"),
            item("3", "Retired rule", "Email Rule", status="Inactive"),
        ]
    )

    configuration = read(board)

    assert [entry.name for entry in configuration.email_configurations] == [
        "Uber receipt interpretation"
    ]
    rules = board.reads[0]["rules"]
    assert rules == [
        {"column_id": STATUS_COLUMN, "compare_value": [1], "operator": "any_of"}
    ]


def test_a_filter_that_did_not_apply_is_refused_rather_than_trimmed() -> None:
    """An unfiltered board would put drafts into a review as agreed rules."""
    board = FakeBoard(
        [UBER, item("2", "Draft rule", "Email Rule", status="Draft")],
        obeys_rules=False,
    )

    with pytest.raises(ConfigurationUnavailable) as refusal:
        read(board)

    assert "did not apply" in str(refusal.value)


def test_each_type_is_ordered_by_its_own_order_column() -> None:
    board = FakeBoard(
        [
            item("1", "Second rule", "Email Rule", order="20"),
            item("2", "First rule", "Email Rule", order="10"),
            item("3", "Second process", "Process", order="20"),
            item("4", "First process", "Process", order="10"),
        ]
    )

    configuration = read(board)

    assert [entry.name for entry in configuration.processes] == [
        "First process",
        "Second process",
    ]
    assert [entry.name for entry in configuration.email_configurations] == [
        "First rule",
        "Second rule",
    ]


def test_an_item_with_no_order_is_read_and_placed_last() -> None:
    """A rule Brian never placed should not become the first one applied."""
    board = FakeBoard(
        [
            item("1", "Unplaced rule", "Email Rule", order=None),
            item("2", "Placed rule", "Email Rule", order="30"),
        ]
    )

    configuration = read(board)

    assert [entry.name for entry in configuration.email_configurations] == [
        "Placed rule",
        "Unplaced rule",
    ]
    assert configuration.email_configurations[1].order is None


def test_types_that_are_not_email_configuration_are_not_returned_as_it() -> None:
    board = FakeBoard(
        [UBER, item("2", "Renewal cadence", "To-Do Rule"), item("3", "Cards", "Reference Data")]
    )

    configuration = read(board)

    assert [entry.name for entry in configuration.email_configurations] == [
        "Uber receipt interpretation"
    ]
    assert configuration.processes == ()


def test_a_missing_column_names_itself_and_what_the_board_has() -> None:
    without_instructions = [
        column for column in COLUMNS if column.title != "Instructions / Logic"
    ]

    with pytest.raises(ConfigurationUnavailable) as refusal:
        read(FakeBoard([UBER], columns=without_instructions))

    message = str(refusal.value)
    assert "Instructions / Logic" in message
    assert "Trigger / Match" in message


def test_a_status_column_without_an_active_label_is_refused() -> None:
    columns = [
        MondayColumn(STATUS_COLUMN, "Status", "status", json.dumps({"labels": {"0": "On"}}))
        if column.column_id == STATUS_COLUMN
        else column
        for column in COLUMNS
    ]

    with pytest.raises(ConfigurationUnavailable) as refusal:
        read(FakeBoard([UBER], columns=columns))

    assert "'Active'" in str(refusal.value)


def test_a_type_column_missing_a_kind_is_refused_rather_than_read_as_empty() -> None:
    """No 'Email Rule' label means every email review reads as unconfigured."""
    columns = [
        MondayColumn(
            TYPE_COLUMN,
            "Configuration Type",
            "dropdown",
            json.dumps({"labels": [{"id": 1, "name": "Process"}]}),
        )
        if column.column_id == TYPE_COLUMN
        else column
        for column in COLUMNS
    ]

    with pytest.raises(ConfigurationUnavailable) as refusal:
        read(FakeBoard([UBER], columns=columns))

    assert "'Email Rule'" in str(refusal.value)


def test_a_configuration_type_that_is_not_built_is_refused_by_name() -> None:
    with pytest.raises(ConfigurationUnavailable) as refusal:
        read(FakeBoard([UBER]), configuration_type="to_do")

    assert "email" in str(refusal.value)


def test_a_monday_failure_is_reported_rather_than_answered_around() -> None:
    board = FakeBoard([UBER], fails=MondayError("Could not contact Monday."))

    with pytest.raises(MondayError):
        read(board)


def test_an_empty_board_is_an_answer_and_not_a_failure() -> None:
    configuration = read(FakeBoard([]))

    assert configuration.processes == ()
    assert configuration.email_configurations == ()


def install(monkeypatch: pytest.MonkeyPatch, board: FakeBoard) -> None:
    @asynccontextmanager
    async def open_client(_token: str) -> AsyncIterator[FakeBoard]:
        yield board

    monkeypatch.setattr(tools_module, "open_monday_client", open_client)


def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    url = f"sqlite:///{tmp_path / 'configuration.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TIMMENY_OS_API_KEY", API_KEY)
    monkeypatch.setenv("MONDAY_API_TOKEN", "monday-token")
    monkeypatch.setenv("MONDAY_ADMIN_OS_CONFIG_BOARD_ID", BOARD_ID)
    monkeypatch.setenv("CAPABILITIES_PATH", str(CONFIG_PATH))
    clear_cache()
    engine_module.dispose_connection()
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    engine_module.dispose_connection()
    clear_cache()


def call(client: TestClient, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        headers=AUTH,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_admin_os_configuration",
                "arguments": arguments if arguments is not None else {"configuration_type": "email"},
            },
        },
    )
    assert response.status_code == 200
    return response.json()["result"]


def test_the_tool_answers_with_the_board_and_when_it_was_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board = FakeBoard([PROCESS, UBER])
    install(monkeypatch, board)
    client = make_client(tmp_path, monkeypatch)

    result = call(client)

    payload = result["structuredContent"]
    assert result["isError"] is False
    assert payload["source"] == "monday"
    assert payload["board_id"] == BOARD_ID
    assert payload["retrieved_at"]
    assert [entry["key"] for entry in payload["processes"]] == ["email_review_process"]
    assert [entry["key"] for entry in payload["email_configurations"]] == [
        "uber_receipt_interpretation"
    ]


def test_the_tool_reads_the_board_every_time_it_is_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board is where Brian changes his mind; a cache is yesterday's rules."""
    board = FakeBoard([UBER])
    install(monkeypatch, board)
    client = make_client(tmp_path, monkeypatch)

    call(client)
    call(client)

    assert len(board.reads) == 2


def test_the_only_configuration_the_tool_offers_is_the_one_that_is_built() -> None:
    """The published enum and the service's refusal have to mean the same thing."""
    published = tools_module.GetAdminOsConfigurationArguments.model_json_schema()
    allowed = published["properties"]["configuration_type"]["const"]

    assert allowed == EMAIL_CONFIGURATION
    assert (
        tools_module.GetAdminOsConfigurationArguments().configuration_type
        == EMAIL_CONFIGURATION
    )


def test_the_tool_is_published_and_reads_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client it holds cannot write: MondayWriter is a different class."""
    board = FakeBoard([UBER])
    install(monkeypatch, board)
    client = make_client(tmp_path, monkeypatch)

    published = client.get("/mcp/tools", headers=AUTH).json()
    assert "get_admin_os_configuration" in published["tool_names"]

    call(client)

    assert not hasattr(board, "create_item")
    assert all("mutation" not in repr(read) for read in board.reads)


def test_asking_for_configuration_that_is_not_built_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeBoard([UBER]))
    client = make_client(tmp_path, monkeypatch)

    result = call(client, {"configuration_type": "to_do"})

    assert result["isError"] is True
    assert "configuration_type" in json.dumps(result["structuredContent"])


def test_an_unconfigured_board_id_is_said_rather_than_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeBoard([UBER]))
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.delenv("MONDAY_ADMIN_OS_CONFIG_BOARD_ID")

    result = call(client)

    assert result["isError"] is True
    assert "MONDAY_ADMIN_OS_CONFIG_BOARD_ID" in result["structuredContent"]["message"]


def test_a_monday_failure_reaches_the_caller_as_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeBoard([UBER], fails=MondayError("Could not contact Monday.")))
    client = make_client(tmp_path, monkeypatch)

    result = call(client)

    assert result["isError"] is True
    assert "Monday" in result["structuredContent"]["message"]
