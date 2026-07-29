"""The published GPT Action contract has to describe the app that exists.

The contract is hand-written rather than generated, so nothing but a test stops
it drifting from the routes it claims to describe — and a GPT calling a route
that has moved fails in front of Brian, not in CI.
"""

from pathlib import Path

import yaml

import main


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPOSITORY_ROOT / "docs/gpt-action-openapi.yaml"
INSTRUCTIONS_PATH = REPOSITORY_ROOT / "docs/gpt-daily-review-instructions.md"
INSTRUCTION_LIMIT = 8000
METHODS = {"get", "post", "patch", "put", "delete"}

REQUIRED_OPERATIONS = {
    ("/review/start", "post"),
    ("/review/continue", "post"),
    ("/review/restart", "post"),
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

    start = document["paths"]["/review/start"]["post"]["responses"]["200"]
    started = start["content"]["application/json"]["schema"]["properties"]
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
