from fastapi import HTTPException

from adminos.capabilities.config import (
    CapabilityConfig,
    CapabilityConfigError,
    LoadedCapabilities,
    UnknownCapability,
    load_capabilities,
)
from adminos.logging import get_logger


logger = get_logger(__name__)


def read_capability_config() -> LoadedCapabilities:
    """Load the capability configuration, or fail the request loudly.

    A broken configuration file is a deployment fault, not a client error: the
    service reports 503 rather than behaving as though no capability exists,
    which would silently present an empty review.
    """
    try:
        return load_capabilities()
    except CapabilityConfigError as exc:
        logger.error("capability configuration unusable: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def read_capability(loaded: LoadedCapabilities, key: str) -> CapabilityConfig:
    try:
        return loaded.get(key)
    except UnknownCapability as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
