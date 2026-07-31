"""The GPT Action contract, served by the deployment that implements it.

A schema pasted into ChatGPT out of a file on someone's laptop is a copy, and a
copy has no way of saying how old it is. The Custom GPT will happily go on
sending last month's request body to this month's API, and the first sign of it
is a refusal in front of Brian.

So the running service publishes the contract it was deployed with, and states
its version and a fingerprint of every request shape in it. Importing from the
URL means the schema and the API are the same commit by construction; the
version endpoint is how an already-imported copy can be checked against what is
actually running.

Both routes are unauthenticated, because ChatGPT's schema import sends no
headers. Neither reveals anything a caller could not already learn by reading
a 401 — the operations, not the data, and every one of them still needs the
API key.
"""

import hashlib
import json
import os
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel


router = APIRouter(tags=["schema"])

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs" / "gpt-action-openapi.yaml"

COMMIT_VARIABLES = ("RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT_SHA")
"""Where the deployment platform records the commit it built, if it does."""


class SchemaVersion(BaseModel):
    """Enough to tell a current import from a stale one."""

    version: str
    request_shape: str
    document_sha256: str
    commit: str | None


def read_contract_text() -> str:
    try:
        return CONTRACT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="The GPT Action contract is missing from this deployment.",
        ) from exc


def request_shape_fingerprint(document: object) -> str:
    """A digest of every request body the contract defines.

    Response shapes and prose are deliberately excluded. A GPT that renders a
    new field it has not been told about is a cosmetic problem; a GPT sending
    a request body the API no longer accepts is a refused execution, so it is
    request shapes that a version has to be honest about.

    Components a body names are resolved into it. A body that says only
    `$ref: PlaybookChange` has exactly the shape of whatever that component
    says today, so hashing the reference rather than the thing referenced
    would let a request shape change under a version that never moved.
    """
    shapes: dict[str, object] = {}
    if isinstance(document, dict):
        paths = document.get("paths")
        if isinstance(paths, dict):
            for path, item in sorted(paths.items()):
                if not isinstance(item, dict):
                    continue
                for method, operation in sorted(item.items()):
                    if not isinstance(operation, dict):
                        continue
                    body = operation.get("requestBody")
                    if body is None:
                        continue
                    shapes[f"{method.upper()} {path}"] = strip_prose(
                        inline(body, document, ())
                    )
    canonical = json.dumps(shapes, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def inline(node: object, document: dict[str, object], seen: tuple[str, ...]) -> object:
    """The node with every component reference replaced by the component.

    A reference already being resolved is left as itself: a component that
    contains itself would otherwise be inlined forever.
    """
    if isinstance(node, list):
        return [inline(value, document, seen) for value in node]
    if not isinstance(node, dict):
        return node

    reference = node.get("$ref")
    if isinstance(reference, str):
        if reference in seen:
            return {"$ref": reference}
        component = find_component(document, reference)
        if component is None:
            return {"$ref": reference}
        return inline(component, document, (*seen, reference))

    return {key: inline(value, document, seen) for key, value in node.items()}


def find_component(document: dict[str, object], reference: str) -> object:
    """The component a local reference names, or None where it names nothing."""
    if not reference.startswith("#/"):
        return None
    found: object = document
    for step in reference[2:].split("/"):
        if not isinstance(found, dict):
            return None
        found = found.get(step)
    return found


def strip_prose(node: object) -> object:
    """The same shape described in better words is the same shape."""
    if isinstance(node, dict):
        return {
            key: strip_prose(value)
            for key, value in sorted(node.items())
            if key not in {"description", "summary", "example", "examples"}
        }
    if isinstance(node, list):
        return [strip_prose(value) for value in node]
    return node


def read_schema_version() -> SchemaVersion:
    raw = read_contract_text()
    document = yaml.safe_load(raw)
    info = document.get("info") if isinstance(document, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return SchemaVersion(
        version=str(version) if version is not None else "unknown",
        request_shape=request_shape_fingerprint(document),
        document_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        commit=deployed_commit(),
    )


def deployed_commit() -> str | None:
    for variable in COMMIT_VARIABLES:
        commit = os.environ.get(variable)
        if commit:
            return commit
    return None


@router.get(
    "/gpt/action-schema.yaml",
    response_class=PlainTextResponse,
    responses={200: {"content": {"application/yaml": {}}}},
)
def gpt_action_schema() -> PlainTextResponse:
    """The contract to import into the Custom GPT, from the running service."""
    return PlainTextResponse(
        content=read_contract_text(),
        media_type="application/yaml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/gpt/action-schema/version", response_model=SchemaVersion)
def gpt_action_schema_version() -> SchemaVersion:
    """What ChatGPT should be showing if its import is current."""
    return read_schema_version()
