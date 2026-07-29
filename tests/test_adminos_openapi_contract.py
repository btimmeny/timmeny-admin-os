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
METHODS = {"get", "post", "patch", "put", "delete"}

REQUIRED_OPERATIONS = {
    ("/review/start", "post"),
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
