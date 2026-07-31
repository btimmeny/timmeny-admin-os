"""Tool input schemas, written out whole.

Pydantic describes a nested model by reference, and a reference is a promise
that the reader will resolve it. Importers have already refused this project's
OpenAPI twice over what they would and would not resolve, so an MCP tool
publishes one self-contained document per tool instead: every definition
inlined, nothing to follow, nothing to get wrong.
"""

from typing import Any

from pydantic import BaseModel


MAXIMUM_DEPTH = 16
"""Deep enough for any tool argument here, shallow enough to catch a cycle."""


class SchemaError(RuntimeError):
    """Raised when a model cannot be published as a self-contained schema."""


def tool_input_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON Schema for a tool's arguments, with every definition inlined."""
    schema = model.model_json_schema(ref_template="#/$defs/{model}")
    definitions = schema.pop("$defs", {})
    resolved = inline(schema, definitions, depth=0, seen=())
    if not isinstance(resolved, dict):
        raise SchemaError(f"{model.__name__} does not describe an object.")
    resolved.setdefault("type", "object")
    resolved.setdefault("properties", {})
    return resolved


def inline(node: Any, definitions: dict[str, Any], depth: int, seen: tuple[str, ...]) -> Any:
    if depth > MAXIMUM_DEPTH:
        raise SchemaError("The schema nests deeper than an argument document should.")

    if isinstance(node, list):
        return [inline(item, definitions, depth + 1, seen) for item in node]
    if not isinstance(node, dict):
        return node

    reference = node.get("$ref")
    if isinstance(reference, str):
        name = reference.rsplit("/", 1)[-1]
        if name in seen:
            raise SchemaError(f"{name} refers to itself, which no tool argument should.")
        target = definitions.get(name)
        if target is None:
            raise SchemaError(f"The schema refers to {name!r}, which it does not define.")
        resolved = inline(target, definitions, depth + 1, seen + (name,))
        if not isinstance(resolved, dict):
            raise SchemaError(f"{name} does not describe an object.")
        alongside = {key: value for key, value in node.items() if key != "$ref"}
        return {
            **resolved,
            **{
                key: inline(value, definitions, depth + 1, seen)
                for key, value in alongside.items()
            },
        }

    return {
        key: inline(value, definitions, depth + 1, seen) for key, value in node.items()
    }


__all__ = ["SchemaError", "tool_input_schema"]
