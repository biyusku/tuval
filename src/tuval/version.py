"""Minimal dotted version type shared by profiles and runtime detection."""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int = 0

    @classmethod
    def parse(cls, text: str) -> Version:
        """Parse ``MAJOR.MINOR`` or ``MAJOR.MINOR.PATCH``; raise ``ValueError`` otherwise."""
        match = _VERSION_RE.match(text.strip())
        if match is None:
            raise ValueError(f"not a version: {text!r}")
        major, minor, patch = match.groups()
        return cls(int(major), int(minor), int(patch) if patch is not None else 0)

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}"
        return f"{base}.{self.patch}" if self.patch else base
