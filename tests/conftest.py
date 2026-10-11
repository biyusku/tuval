from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from tuval.log import get_logger


@pytest.fixture(autouse=True)
def reset_tuval_logger() -> Iterator[None]:
    """Undo ``configure_logging`` so no test logs into a stream an earlier test closed."""
    yield
    logger = get_logger()
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
