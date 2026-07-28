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
DOCUMENTED_PREFIXES = ("/review", "/learning")
METHODS = {"get", "post", "patch", "put", "delete"}


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


def test_every_review_and_learning_route_is_documented() -> None:
    documented = operations(read_contract()["paths"])
    live = {
        operation
        for operation in operations(main.app.openapi()["paths"])
        if operation[0].startswith(DOCUMENTED_PREFIXES)
    }

    assert live - documented == set()


def test_the_contract_never_offers_permanent_deletion() -> None:
    assert "gmail.trash" not in CONTRACT_PATH.read_text()
