"""The MCP endpoint: one POST, one answer.

ChatGPT can hold a remote MCP connector and the Gmail app in one conversation,
which is the arrangement this whole milestone needs — the client reads the
mailbox, Admin OS owns the process. The OpenAPI Action contract is untouched
and keeps working; this is a second door into the same building.

Streamable HTTP: `POST /mcp` for every message, no session header, no
server-initiated stream. A client that says it accepts `text/event-stream` is
answered with one SSE event, because that is what the remote MCP clients in
use are proven against; one that asks only for JSON gets JSON. Either way it
is one request and one answer.

Authenticated exactly like everything else that touches data — `X-API-Key` or
`Authorization: Bearer` — and stateless: nothing for a redeploy to lose.
"""

import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from adminos.api.security import require_api_key
from adminos.mcp import protocol
from adminos.mcp.tools import TOOL_NAMES


router = APIRouter(tags=["mcp"])

MCP_PATH = "/mcp"

PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"

EVENT_STREAM = "text/event-stream"

ACCEPTED = 202
NOT_ALLOWED = 405


@router.post(MCP_PATH)
async def call_mcp(
    request: Request, _: None = Depends(require_api_key)
) -> Response:
    """Answer one JSON-RPC message.

    A notification has no id and gets no body, only a 202 — the client is
    telling the server something, not asking it.
    """
    body = (await request.body()).decode("utf-8", errors="replace")
    asked = request.headers.get(PROTOCOL_VERSION_HEADER) or protocol.PROTOCOL_VERSION
    answer, version = protocol.handle_json(body, asked)

    if answer is None:
        return Response(status_code=ACCEPTED, headers={PROTOCOL_VERSION_HEADER: version})

    headers = {PROTOCOL_VERSION_HEADER: version}
    if accepts_stream(request):
        return Response(
            content=f"event: message\ndata: {json.dumps(answer)}\n\n",
            media_type=EVENT_STREAM,
            headers={**headers, "Cache-Control": "no-cache"},
        )
    return JSONResponse(answer, headers=headers)


@router.get(MCP_PATH)
async def no_stream() -> Response:
    """There is no server-initiated stream here, and saying so beats hanging."""
    return JSONResponse(
        protocol.error_response(
            None,
            protocol.METHOD_NOT_FOUND,
            "This server does not open a stream. Send tool calls as POST requests.",
        ),
        status_code=NOT_ALLOWED,
        headers={"Allow": "POST"},
    )


@router.delete(MCP_PATH)
async def no_session() -> Response:
    """No session is kept, so there is none to end."""
    return JSONResponse(
        protocol.error_response(
            None,
            protocol.METHOD_NOT_FOUND,
            "This server keeps no session, so there is nothing to delete.",
        ),
        status_code=NOT_ALLOWED,
        headers={"Allow": "POST"},
    )


@router.get("/mcp/tools")
async def published_tools(_: None = Depends(require_api_key)) -> dict[str, object]:
    """What this server offers, readable without speaking JSON-RPC.

    For checking a deployment against what the instructions tell the GPT to
    call, which is the failure this project has already had twice with the
    OpenAPI contract.
    """
    return {
        "server": protocol.SERVER_NAME,
        "version": protocol.SERVER_VERSION,
        "protocol_version": protocol.PROTOCOL_VERSION,
        "tool_names": list(TOOL_NAMES),
        "tools": protocol.list_tools()["tools"],
    }


def accepts_stream(request: Request) -> bool:
    """Answer as a stream whenever the client says it reads one.

    The transport allows either, and a client that accepts both is entitled to
    either — but the remote MCP clients this has to work with are proven
    against servers that stream, and a working connector matters more here
    than the simpler response.
    """
    return EVENT_STREAM in request.headers.get("accept", "")


__all__ = ["router"]
