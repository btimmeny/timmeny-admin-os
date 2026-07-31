"""JSON-RPC, the small part of MCP this server actually needs.

Stateless on purpose: no session id, no server-initiated stream, no
subscriptions. Every tool call is one request, one transaction, one response,
which is what the existing service already is and what Railway already runs.
A session would be state to keep in sync with the database's, and there is
nothing here that a second round trip cannot ask for again.
"""

import json
from dataclasses import dataclass
from typing import Any

from adminos.logging import get_logger
from adminos.mcp import tools


logger = get_logger(__name__)

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "timmeny-admin-os"
SERVER_TITLE = "Timmeny Admin OS"
SERVER_VERSION = "1.0.0"
"""The tool contract's own version, kept apart from the OpenAPI contract's.

Two transports, two sets of clients, two things that can go stale separately.
One number covering both would move when the other changed and tell a client
nothing about the tools it holds.
"""

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = (
    "Admin OS owns the administrative review: the phases, the groups, what a "
    "reviewed thread must state, and when a phase is done. Start with "
    "start_admin_review, read the playbook it pins, read the Gmail Inbox "
    "yourself through the Gmail app, classify every thread into exactly one "
    "group from the playbook, then record and complete. Admin OS reads no mail "
    "and changes nothing in Gmail."
)


class JsonRpcError(Exception):
    """An error in the request itself, as against a tool that refused."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True)
class Request:
    method: str
    params: dict[str, Any]
    id: Any | None
    is_notification: bool


def handle_json(body: str, version: str) -> tuple[dict[str, Any] | None, str]:
    """Read one JSON-RPC message and answer it. Returns the answer and version."""
    try:
        message = json.loads(body)
    except json.JSONDecodeError as exc:
        return error_response(None, PARSE_ERROR, f"The request is not valid JSON: {exc}"), version
    return handle_message(message, version)


def handle_message(
    message: Any, version: str = PROTOCOL_VERSION
) -> tuple[dict[str, Any] | None, str]:
    if isinstance(message, list):
        return (
            error_response(
                None,
                INVALID_REQUEST,
                "This server answers one request at a time; batches are not supported.",
            ),
            version,
        )

    try:
        request = read_request(message)
    except JsonRpcError as exc:
        identifier = message.get("id") if isinstance(message, dict) else None
        return error_response(identifier, exc.code, exc.message, exc.data), version

    try:
        result, version = dispatch(request, version)
    except JsonRpcError as exc:
        if request.is_notification:
            return None, version
        return error_response(request.id, exc.code, exc.message, exc.data), version
    except Exception as exc:  # a fault here is ours, and the caller still needs an answer
        logger.exception("mcp method %s failed", request.method)
        if request.is_notification:
            return None, version
        return (
            error_response(request.id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"),
            version,
        )

    if request.is_notification:
        return None, version
    return {"jsonrpc": "2.0", "id": request.id, "result": result}, version


def read_request(message: Any) -> Request:
    if not isinstance(message, dict):
        raise JsonRpcError(INVALID_REQUEST, "A JSON-RPC message must be an object.")
    if message.get("jsonrpc") != "2.0":
        raise JsonRpcError(INVALID_REQUEST, "Only JSON-RPC 2.0 is supported.")

    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(INVALID_REQUEST, "The message names no method.")

    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JsonRpcError(INVALID_PARAMS, "Parameters must be an object.")

    return Request(
        method=method,
        params=params,
        id=message.get("id"),
        is_notification="id" not in message,
    )


def dispatch(request: Request, version: str) -> tuple[dict[str, Any], str]:
    if request.method == "initialize":
        return initialize(request.params)
    if request.method.startswith("notifications/"):
        return {}, version
    if request.method == "ping":
        return {}, version
    if request.method == "tools/list":
        return list_tools(), version
    if request.method == "tools/call":
        return call_tool(request.params), version
    raise JsonRpcError(METHOD_NOT_FOUND, f"This server has no method {request.method!r}.")


def initialize(params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Agree a protocol version, preferring the client's when it is one we speak."""
    asked = params.get("protocolVersion")
    agreed = asked if isinstance(asked, str) and asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION
    return (
        {
            "protocolVersion": agreed,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": SERVER_NAME,
                "title": SERVER_TITLE,
                "version": SERVER_VERSION,
            },
            "instructions": INSTRUCTIONS,
        },
        agreed,
    )


def list_tools() -> dict[str, Any]:
    return {"tools": [tool.definition() for tool in tools.TOOLS]}


def call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise JsonRpcError(INVALID_PARAMS, "A tool call must name a tool.")

    arguments = params.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(INVALID_PARAMS, "Tool arguments must be an object.")

    try:
        result = tools.call(name, arguments)
    except tools.ToolError as exc:
        raise JsonRpcError(INVALID_PARAMS, str(exc)) from exc

    return {
        "content": [{"type": "text", "text": json.dumps(result.payload, indent=2)}],
        "structuredContent": result.payload,
        "isError": result.is_error,
    }


def error_response(
    identifier: Any, code: int, message: str, data: Any | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": identifier, "error": error}


__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_VERSION",
    "SUPPORTED_VERSIONS",
    "JsonRpcError",
    "handle_json",
    "handle_message",
    "list_tools",
]
