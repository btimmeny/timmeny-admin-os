"""The schema ChatGPT imports has to be the schema this service implements.

The Custom GPT holds a copy of the Action contract. A copy is silent about its
age: nothing in ChatGPT says which version it took, and a request body that was
right last month fails in front of Brian rather than in CI. So the deployment
serves the contract itself, and states its version, and these tests hold that
promise: the served document is the repository's, the version moves whenever a
request shape does, and the execution request the contract describes is the one
the API actually requires.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

import main
from adminos.api.schema import request_shape_fingerprint


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPOSITORY_ROOT / "docs/gpt-action-openapi.yaml"

REQUEST_SHAPES: dict[str, str] = {
    "0.11.0": "6276e313ba076bf05bd2c7c7df633a6099bfe1ef8011ca9c0728067c054812f5",
    "0.12.0": "0e8d7f37f4a88e9e3086e99968d06253f4670c258ff0bb5ddfd97a9a1c021b05",
    "0.12.1": "0e8d7f37f4a88e9e3086e99968d06253f4670c258ff0bb5ddfd97a9a1c021b05",
    "0.13.0": "bfe017ada84890a3e901660f575f59c7855f55e77e3a30af0ef6958d394a1fed",
    "0.14.0": "bfe017ada84890a3e901660f575f59c7855f55e77e3a30af0ef6958d394a1fed",
    "0.15.0": "002e9d7750d783f20fd319aa79e31cd256553ae27c7a90d3171f77a6b6059dbc",
    "0.16.0": "002e9d7750d783f20fd319aa79e31cd256553ae27c7a90d3171f77a6b6059dbc",
    "0.17.0": "002e9d7750d783f20fd319aa79e31cd256553ae27c7a90d3171f77a6b6059dbc",
    "0.18.0": "002e9d7750d783f20fd319aa79e31cd256553ae27c7a90d3171f77a6b6059dbc",
    "0.19.0": "878bfb8aaa7990be4c13368c5b07d82ee8bda9a10df319db5b54ba9bf03767c7",
    "0.19.1": "878bfb8aaa7990be4c13368c5b07d82ee8bda9a10df319db5b54ba9bf03767c7",
    "0.19.2": "878bfb8aaa7990be4c13368c5b07d82ee8bda9a10df319db5b54ba9bf03767c7",
    "0.20.0": "ca81b5e87e3ea52ba755e30cccd1c47b9d9387e977c55990b5c257b8438dd001",
}
"""Every version of the contract, and the request shapes it published.

A GPT sending a body the API no longer accepts is a refused execution, and the
only way an already-imported copy can be told apart from a current one is its
version. So a change to any request body has to arrive with a new version:
editing the shape under an existing one is what this record makes visible.

Two versions may share a shape — 0.12.1 shortened descriptions for ChatGPT's
importer and asked for nothing new — but one shape must never be published
under two versions with different content, which is what the mapping checks.

Everything up to 0.19.2 was fingerprinted without resolving the components a
body references, so those digests are a record of what was published rather
than something recomputable from the document today. From 0.20.0 the
referenced component is part of the shape, which is what it always was to a
GPT reading the contract.
"""

IMPORTER_PROSE_LIMIT = 300
"""What ChatGPT allows a description or summary, in characters.

Not ours to choose, and not negotiable: over it, the import fails outright.
"""

EXECUTE_PATH = "/review/runs/{run_id}/actions/execute"
PREPARE_PATH = "/review/runs/{run_id}/actions/prepare"


@pytest.fixture()
def client() -> TestClient:
    """The schema routes read a file and an environment variable, nothing else."""
    return TestClient(main.app)


def contract() -> dict[str, Any]:
    document = yaml.safe_load(CONTRACT_PATH.read_text())
    assert isinstance(document, dict)
    return document


def request_body(document: dict[str, Any], path: str) -> dict[str, Any]:
    body = document["paths"][path]["post"]["requestBody"]
    schema = body["content"]["application/json"]["schema"]
    assert isinstance(schema, dict)
    return schema


def resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """The schema itself, or the component it names."""
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return schema
    resolved = document["components"]["schemas"][reference.rsplit("/", 1)[1]]
    assert isinstance(resolved, dict)
    return resolved


def bodies(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every request and response body the contract publishes, with its route."""
    found: list[tuple[str, dict[str, Any]]] = []
    for path, item in document["paths"].items():
        for method, operation in item.items():
            body = operation.get("requestBody")
            if body is not None:
                schema = body["content"]["application/json"]["schema"]
                found.append((f"{method.upper()} {path} request", schema))
            for status, response in operation.get("responses", {}).items():
                content = response.get("content")
                if content is None:
                    continue
                for media, described in content.items():
                    schema = described.get("schema")
                    if schema is not None:
                        found.append((f"{method.upper()} {path} {status} {media}", schema))
    return found


def test_the_deployment_serves_the_contract_it_implements(client: TestClient) -> None:
    """Importing from the running service is the only way to import the truth."""
    response = client.get("/gpt/action-schema.yaml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/yaml")
    assert response.text == CONTRACT_PATH.read_text()


def test_the_schema_can_be_read_without_the_api_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ChatGPT's import sends no headers, and the contract is not a secret."""
    monkeypatch.setenv("TIMMENY_OS_API_KEY", "a-key-this-request-does-not-send")

    assert client.get("/gpt/action-schema.yaml").status_code == 200
    assert client.get("/gpt/action-schema/version").status_code == 200
    assert client.post("/review/start", json={}).status_code == 401


def test_the_version_endpoint_says_what_was_deployed(client: TestClient) -> None:
    """What an import must match, in the three ways it can be checked."""
    body = client.get("/gpt/action-schema/version").json()

    assert body["version"] == contract()["info"]["version"]
    assert body["request_shape"] == request_shape_fingerprint(contract())
    assert len(body["document_sha256"]) == 64


def test_the_version_endpoint_names_the_deployed_commit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API and the schema are one deployment, so one commit answers for both."""
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")

    assert client.get("/gpt/action-schema/version").json()["commit"] == "abc123"


def test_a_changed_request_shape_takes_a_new_version() -> None:
    """Change what the GPT must send, and the version has to say so."""
    document = contract()
    version = document["info"]["version"]
    fingerprint = request_shape_fingerprint(document)

    assert version in REQUEST_SHAPES, (
        f"Version {version} is not recorded in REQUEST_SHAPES. Add it with its "
        "fingerprint rather than reusing a published version."
    )
    assert REQUEST_SHAPES[version] == fingerprint, (
        f"The request shapes changed under version {version}. Increment "
        "info.version in docs/gpt-action-openapi.yaml and record the new "
        f"fingerprint {fingerprint} against it, so an imported copy can be "
        "told apart from this one."
    )


def prose(node: object, path: str) -> list[tuple[str, str]]:
    """Every description and summary in the document, with where it is."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"description", "summary"} and isinstance(value, str):
                found.append((f"{path}.{key}", value))
            else:
                found.extend(prose(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(prose(value, f"{path}[{index}]"))
    return found


def test_no_description_is_longer_than_the_importer_accepts() -> None:
    """ChatGPT refuses the whole import over one long description.

    "description has length 894 exceeding limit of 300" is not a warning: the
    schema does not import, so every operation is unavailable until the prose
    is cut. It has happened twice, and prose grows every increment, so the
    limit belongs in the suite rather than in whoever is editing the file.
    """
    too_long = [
        (where, len(text))
        for where, text in prose(contract(), "")
        if len(text) > IMPORTER_PROSE_LIMIT
    ]

    assert not too_long, (
        "ChatGPT's importer rejects a schema whose descriptions run over "
        f"{IMPORTER_PROSE_LIMIT} characters, and rejects the document, not "
        f"the sentence: {too_long}"
    )


def test_every_published_body_says_what_is_in_it() -> None:
    """`type: object` and nothing else is a body ChatGPT will not import.

    "object schema missing properties" is refused the same way an over-long
    description is: the document does not import, so five responses left as
    bare objects cost every operation in it. Naming the fields is also the
    only way the GPT knows what it was handed.

    It is the body itself the importer reads, not every object inside it: the
    same document it refused carried maps like `counts` and request `body`
    fields that name no properties because their keys are not fixed, and said
    nothing about them. So this looks where the importer looks.
    """
    document = contract()
    published = [(where, resolve(document, schema)) for where, schema in bodies(document)]
    shapeless = [
        where
        for where, schema in published
        if schema.get("type") == "object" and "properties" not in schema
    ]

    assert not shapeless, (
        "ChatGPT refuses a document with an object body that names no "
        f"properties: {shapeless}. Give it a shape, or a $ref to one."
    )


def test_preparation_promises_the_scope_execution_demands() -> None:
    """The three fields the GPT carries from one call to the next."""
    prepared = request_body(contract(), PREPARE_PATH)
    returned = contract()["paths"][PREPARE_PATH]["post"]["responses"]["200"]
    properties = returned["content"]["application/json"]["schema"]["properties"]

    assert set(prepared["properties"]) == {
        "capability_key",
        "item_ids",
        "entire_capability",
    }
    for field in ("scope_id", "prepared_item_ids", "action_ids"):
        assert field in properties


def test_execution_requires_the_exact_prepared_scope() -> None:
    """Named, restated, and confirmed: all four, or the request is not valid."""
    schema = request_body(contract(), EXECUTE_PATH)

    assert set(schema["required"]) == {"scope_id", "item_ids", "action_ids", "confirm"}
    assert set(schema["properties"]) == {
        "scope_id",
        "item_ids",
        "action_ids",
        "confirm",
    }


def test_the_contract_requires_what_the_api_requires() -> None:
    """A documented field the API ignores, or demands, is a lie either way.

    `confirm` is the one deliberate difference: the model takes it as false by
    default so that an unconfirmed execution can answer "send confirm=true"
    rather than a validation error that says nothing about why. The contract
    still requires it, because a GPT that leaves it out is wrong.
    """
    documented = request_body(contract(), EXECUTE_PATH)
    live = main.app.openapi()["components"]["schemas"]["ExecuteRequest"]

    assert set(documented["properties"]) == set(live["properties"])
    assert set(documented["required"]) - {"confirm"} == set(live["required"])
    assert "confirm" not in live["required"]


def test_the_lifecycle_operations_are_published_with_the_routes_they_call() -> None:
    """A review resumed or restarted has to be asked for by name."""
    paths = contract()["paths"]

    assert paths["/review/continue"]["post"]["operationId"] == "continueDailyReview"
    assert paths["/review/restart"]["post"]["operationId"] == "restartDailyReview"
    live = {route.path for route in main.app.routes}
    assert {"/review/continue", "/review/restart"} <= live


def test_the_review_response_names_the_review_it_is_about() -> None:
    """The GPT reads the lifecycle rather than inferring it from the rows."""
    document = contract()
    returned = document["paths"]["/review/start"]["post"]["responses"]["200"]
    schema = returned["content"]["application/json"]["schema"]
    properties = resolve(document, schema)["properties"]

    for field in (
        "review_id",
        "review_date",
        "revision",
        "status",
        "started_at",
        "completed_at",
        "abandoned_at",
        "evidence_refresh_at",
        "prompt",
    ):
        assert field in properties, field
    assert set(properties["status"]["enum"]) == {
        "not_started",
        "in_progress",
        "awaiting_actions",
        "completed",
        "abandoned",
    }
