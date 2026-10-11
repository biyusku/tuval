from __future__ import annotations

import logging

import pytest

from tuval.log import configure_logging, get_logger


@pytest.mark.parametrize(
    ("verbosity", "level"),
    [
        (-1, logging.WARNING),
        (0, logging.WARNING),
        (1, logging.INFO),
        (2, logging.DEBUG),
        (9, logging.DEBUG),
    ],
)
def test_verbosity_is_clamped(verbosity: int, level: int) -> None:
    configure_logging(verbosity)
    assert get_logger().level == level


def test_repeated_configuration_keeps_one_handler() -> None:
    configure_logging(0)
    configure_logging(1)
    assert len(get_logger().handlers) == 1


def test_messages_go_to_current_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(0)
    get_logger("test").warning("disk is %s", "full")
    assert capsys.readouterr().err == "tuval: WARNING: disk is full\n"
