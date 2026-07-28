import logging
import os
import sys


LOG_LEVEL_VARIABLE = "LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def get_logger(name: str) -> logging.Logger:
    configure_root_logger()
    return logging.getLogger(name)


def configure_root_logger() -> None:
    root_logger = logging.getLogger("adminos")
    if root_logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(handler)
    root_logger.setLevel(os.getenv(LOG_LEVEL_VARIABLE) or DEFAULT_LOG_LEVEL)
