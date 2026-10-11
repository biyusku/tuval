from __future__ import annotations

import pytest

from tuval.version import Version


def test_parse_two_and_three_components() -> None:
    assert Version.parse("11.10") == Version(11, 10, 0)
    assert Version.parse(" 11.10.2 ") == Version(11, 10, 2)


def test_str_omits_zero_patch() -> None:
    assert str(Version(11, 10)) == "11.10"
    assert str(Version(10, 0, 1)) == "10.0.1"


def test_ordering_is_numeric_not_lexical() -> None:
    assert Version(11, 9) < Version(11, 10)
    assert Version(11, 10) < Version(11, 10, 1)
    assert Version(10, 20) < Version(11, 0)


@pytest.mark.parametrize("text", ["", "11", "11.x", "v11.10", "11.10.1.1", "11.-1"])
def test_parse_rejects_malformed(text: str) -> None:
    with pytest.raises(ValueError, match="not a version"):
        Version.parse(text)
