import os

from fastapi import Header, HTTPException


API_KEY_VARIABLE = "TIMMENY_OS_API_KEY"


def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None

    return token


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    """Authenticate a request, refusing to serve when no key is configured.

    Unlike the legacy `verify_api_key` helper this fails closed. Coordination
    endpoints act on Gmail and Monday, so an unset key must not make them
    public.
    """
    expected_api_key = os.getenv(API_KEY_VARIABLE)
    if not expected_api_key:
        raise HTTPException(
            status_code=503,
            detail=f"{API_KEY_VARIABLE} is not configured.",
        )

    provided_api_key = x_api_key or extract_bearer_token(authorization)
    if provided_api_key != expected_api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key.",
        )
