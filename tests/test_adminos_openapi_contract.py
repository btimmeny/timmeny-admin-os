"""The published GPT Action contract has to describe the app that exists.

The contract is hand-written rather than generated, so nothing but a test stops
it drifting from the routes it claims to describe — and a GPT calling a route
that has moved fails in front of Brian, not in CI.
"""

import re
from pathlib import Path

import yaml

import main
from adminos.api.review import RestartActionResponse
from adminos.mcp.tools import TOOL_NAMES


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPOSITORY_ROOT / "docs/gpt-action-openapi.yaml"
INSTRUCTIONS_PATH = REPOSITORY_ROOT / "docs/gpt-daily-review-instructions.md"
INSTRUCTION_LIMIT = 8000
METHODS = {"get", "post", "patch", "put", "delete"}

REQUIRED_OPERATIONS = {
    ("/review/start", "post"),
    ("/review/continue", "post"),
    ("/review/restart", "post"),
    ("/review/runs/{run_id}/plan", "post"),
    ("/review/runs/{run_id}", "get"),
    ("/review/runs/{run_id}/groups/{capability_key}", "get"),
    ("/review/runs/{run_id}/items/{item_id}/decision", "post"),
    ("/review/runs/{run_id}/groups/{capability_key}/decisions", "post"),
    ("/review/runs/{run_id}/actions/prepare", "post"),
    ("/review/runs/{run_id}/actions/execute", "post"),
    ("/review/runs/{run_id}/items/{item_id}/send-draft", "post"),
    ("/learning/rules", "post"),
    ("/learning/rules/{rule_id}/confirm", "post"),
    ("/learning/rules/{rule_id}/promote", "post"),
}
"""What the Daily GPT cannot work without.

The contract is deliberately smaller than the API: routes for reading action
history and learning events exist for operators, and adding every one of them
would only give the GPT more ways to go wrong. These are the operations the
conversation depends on, so losing one is a break rather than a trim.
"""

PERMANENT_DELETION = ("messages.delete", "threads.delete", "permanently delete")


def read_contract() -> dict[str, object]:
    return yaml.safe_load(CONTRACT_PATH.read_text())


def response_schema(document: dict, path: str, method: str) -> dict:
    """The 200 schema, through the component where the response names one."""
    schema = document["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    reference = schema.get("$ref")
    if reference is None:
        return schema
    return document["components"]["schemas"][reference.rsplit("/", 1)[1]]


def operations(paths: dict[str, dict[str, object]]) -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, item in paths.items()
        for method in item
        if method in METHODS
    }


def test_every_documented_operation_exists() -> None:
    documented = operations(read_contract()["paths"])
    live = operations(main.app.openapi()["paths"])

    assert documented - live == set()


def test_the_operations_the_gpt_depends_on_are_documented_and_live() -> None:
    documented = operations(read_contract()["paths"])
    live = operations(main.app.openapi()["paths"])

    assert REQUIRED_OPERATIONS - documented == set()
    assert REQUIRED_OPERATIONS - live == set()


def test_the_contract_never_offers_permanent_deletion() -> None:
    """Trash is offered; destroying mail is not, and is not describable here."""
    contract = CONTRACT_PATH.read_text()

    assert "move_gmail_thread_to_trash" in contract
    for phrase in PERMANENT_DELETION:
        assert phrase not in contract


def test_the_contract_says_a_move_names_its_folder() -> None:
    """A GPT reading only this must know a move is refused without a folder."""
    contract = CONTRACT_PATH.read_text()

    assert "move_gmail_thread_to_label" in contract
    assert '{"label": "Later"}' in contract


def test_the_gpt_instructions_fit_the_field_they_are_pasted_into() -> None:
    """ChatGPT truncates at 8,000 characters, and silently: the tail is the Never list."""
    instructions = INSTRUCTIONS_PATH.read_text()

    assert len(instructions) <= INSTRUCTION_LIMIT, (
        f"{len(instructions)} characters, {len(instructions) - INSTRUCTION_LIMIT} over. "
        "Cut prose rather than a safeguard."
    )


def test_every_operation_the_instructions_name_is_one_the_contract_publishes() -> None:
    """An instruction naming an operation the GPT was never given is a dead end.

    Brian's GPT read "call restartDailyReview" and had no such tool, because
    the schema it held predated the route. Named operations and published ones
    are checked against each other here so the mismatch is CI's problem.
    """
    document = yaml.safe_load(CONTRACT_PATH.read_text())
    published = {
        operation["operationId"]
        for path in document["paths"].values()
        for method, operation in path.items()
        if method in METHODS and "operationId" in operation
    }
    instructions = INSTRUCTIONS_PATH.read_text()
    named = {
        token
        for token in re.findall(r"`([a-z][A-Za-z]+)`", instructions)
        if re.search(r"[A-Z]", token)
    }

    assert named, "The instructions name no operations at all."
    assert named <= published, (
        f"The instructions call {sorted(named - published)}, which the schema does not "
        "publish. Either add the operation or stop naming it."
    )
    for lifecycle in ("startDailyReview", "continueDailyReview", "restartDailyReview"):
        assert lifecycle in named


def test_every_operation_a_response_offers_is_one_the_gpt_was_given() -> None:
    """An offer the GPT cannot take is worse than no offer at all.

    Admin OS answers with the operation to call next — `beginReviewPlan`,
    `prepareReviewActions`, the choices on a session prompt — so a response
    can name a tool the schema never published, and the GPT is left with a
    next step it cannot reach and a conversation that stalls in front of
    Brian. Every name the code hands out is checked against what is published.

    There are two contracts to be published by now: the OpenAPI Action schema
    and the MCP tool list. A next step is reachable if either one offers it,
    and reachable by no other means.
    """
    published = published_operations() | set(TOOL_NAMES)
    offered = {
        (source.relative_to(REPOSITORY_ROOT).as_posix(), name)
        for source in (REPOSITORY_ROOT / "adminos").rglob("*.py")
        for name in re.findall(r"""["']?operation["']?\s*[:=]\s*["'](\w+)["']""", source.read_text())
    }
    restart = RestartActionResponse(body={})
    offered.add(("adminos/api/review.py", restart.name))

    assert offered, "No operation is offered anywhere, which cannot be right."
    unpublished = sorted(f"{where}: {name}" for where, name in offered if name not in published)
    assert not unpublished, (
        f"These responses offer an operation neither contract publishes: {unpublished}. "
        "Publish it, or stop offering it."
    )
    assert (restart.method, restart.path) in {
        (method.upper(), path)
        for path, item in read_contract()["paths"].items()
        for method in item
        if method in METHODS
    }


def published_operations() -> set[str]:
    return {
        operation["operationId"]
        for path in read_contract()["paths"].values()
        for method, operation in path.items()
        if method in METHODS and "operationId" in operation
    }


def test_the_contract_says_refreshing_mail_is_a_restart() -> None:
    """"Check again" answered by starting again is a check that never happened."""
    document = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = document["components"]["schemas"]
    restart = document["paths"]["/review/restart"]["post"]["description"]
    instructions = INSTRUCTIONS_PATH.read_text()

    assert "RestartAction" in schemas
    assert schemas["RestartAction"]["properties"]["name"]["enum"] == ["restartDailyReview"]
    for phrase in ("refresh mail", "check again"):
        assert phrase in restart.lower()
        assert phrase in instructions.lower()
    for response in ("/review/start", "/review/runs/{run_id}"):
        method = "post" if response == "/review/start" else "get"
        properties = response_schema(document, response, method)["properties"]
        assert properties["restart_available"]["type"] == "boolean"
        assert (
            properties["restart_action"]["$ref"] == "#/components/schemas/RestartAction"
        )


def test_the_contract_says_every_entry_reads_the_mailbox() -> None:
    """A review is a snapshot, and the contract has to say which mailbox."""
    document = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = document["components"]["schemas"]
    start = document["paths"]["/review/start"]["post"]["description"]
    resume = document["paths"]["/review/continue"]["post"]["description"]
    instructions = INSTRUCTIONS_PATH.read_text()

    assert "reads Gmail and reviews what it says now" in start
    assert "The only operation that resumes" in resume
    assert "Superseded" in schemas
    for response in ("/review/start", "/review/runs/{run_id}"):
        method = "post" if response == "/review/start" else "get"
        properties = response_schema(document, response, method)["properties"]
        assert properties["snapshot_at"]["format"] == "date-time"
        assert properties["supersedes_review_id"]["type"] == "string"
        assert properties["superseded"]["$ref"] == "#/components/schemas/Superseded"
    assert "snapshot" in instructions.lower()
    assert "only when he asks to continue" in instructions


def test_the_contract_and_the_instructions_open_with_the_playbook() -> None:
    """An orientation the GPT composes is one nobody agreed to and none can test."""
    document = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = document["components"]["schemas"]
    instructions = INSTRUCTIONS_PATH.read_text()

    assert "ReviewOpening" in schemas
    opening = schemas["ReviewOpening"]
    assert set(opening["properties"]) == {"mode", "text"}
    assert opening["properties"]["mode"]["enum"] == ["new", "resumed"]
    assert (
        schemas["ReviewPlan"]["properties"]["opening"]["$ref"]
        == "#/components/schemas/ReviewOpening"
    )
    assert "plan.opening.text" in instructions
    assert "our admin playbook" in instructions
    assert "How can I help?" in instructions


def test_the_gpt_instructions_name_the_lifecycle_operations() -> None:
    """An operation the GPT is never told about is an operation it never calls."""
    instructions = INSTRUCTIONS_PATH.read_text()

    for operation in ("startDailyReview", "continueDailyReview", "restartDailyReview"):
        assert operation in instructions
    assert "review_id" in instructions

def test_the_contract_says_a_decision_is_not_a_done_thing() -> None:
    """The GPT reported three threads deleted on the strength of a decision.

    What it read was a decision response and a review that had moved on, so
    the contract now names the place that says otherwise, in every response
    the GPT sees after deciding.
    """
    document = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = document["components"]["schemas"]

    assert "OutstandingExecution" in schemas
    outstanding = schemas["OutstandingExecution"]
    assert set(outstanding["properties"]) >= {
        "capability_key",
        "item_ids",
        "operation",
        "method",
        "path",
        "body",
        "message",
    }

    started = response_schema(document, "/review/start", "post")["properties"]
    assert "outstanding_execution" in started

    decisions = document["paths"]["/review/runs/{run_id}/groups/{capability_key}/decisions"]
    decided = decisions["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert "outstanding_execution" in decided["properties"]["run"]["properties"]


def test_the_contract_says_which_name_addresses_a_group() -> None:
    """`admin.v2` was sent as a capability key, and it is a policy version."""
    document = yaml.safe_load(CONTRACT_PATH.read_text())
    group = document["paths"]["/review/runs/{run_id}/groups/{capability_key}"]["get"]
    schema = group["responses"]["200"]["content"]["application/json"]["schema"]

    described = schema["properties"]["capability_key"]["description"]
    assert "policy_version" in described
    assert "screen_id" in described
