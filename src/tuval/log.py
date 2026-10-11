"""Logging setup.

Diagnostics go to stderr through :mod:`logging`; command *results* are written by the
CLI to an injected text stream so they can be captured in tests and piped by users.
"""

from __future__ import annotations

import logging
import sys

LOGGER_NAME = "tuval"
_VERBOSITY_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the package logger or one of its children."""
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")


def configure_logging(verbosity: int) -> None:
    """Configure stderr logging; ``verbosity`` is the number of ``-v`` flags."""
    index = max(0, min(verbosity, len(_VERBOSITY_LEVELS) - 1))
    logger = get_logger()
    logger.setLevel(_VERBOSITY_LEVELS[index])
    # Rebind on every call: sys.stderr may have been replaced since the last one.
    for old in list(logger.handlers):
        logger.removeHandler(old)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("tuval: %(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
