"""The compliance guard.

tuval must never weaken, bypass or tamper with a vendor's licensing, activation or
genuine-software checks, and must never write into vendor-owned install locations.
Everything here is deliberately conservative: a false positive costs a user one bug
report, a false negative could cost them their licence. See docs/COMPLIANCE.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath, PurePosixPath

from tuval.profiles import Profile, RegistryValue


class Rule(StrEnum):
    LICENSING_DLL_OVERRIDE = "TUV001"
    REGISTRY_OUTSIDE_ALLOWLIST = "TUV002"
    RAW_WINEDLLOVERRIDES = "TUV003"
    LICENSING_EXECUTABLE = "TUV004"
    VENDOR_DIRECTORY_WRITE = "TUV005"
    HOSTS_FILE_WRITE = "TUV006"


RULE_DESCRIPTIONS: dict[Rule, str] = {
    Rule.LICENSING_DLL_OVERRIDE: "DLL override targets a licensing/activation component",
    Rule.REGISTRY_OUTSIDE_ALLOWLIST: "registry write outside the Wine/display allowlist",
    Rule.RAW_WINEDLLOVERRIDES: "raw WINEDLLOVERRIDES bypasses per-DLL review",
    Rule.LICENSING_EXECUTABLE: "profile launches a licensing/activation component",
    Rule.VENDOR_DIRECTORY_WRITE: "write into a vendor-owned install or licensing directory",
    Rule.HOSTS_FILE_WRITE: "write to a hosts file (used to block activation servers)",
}

# Directory names that only ever hold licensing / activation / genuine-check components.
LICENSING_DIR_NAMES = frozenset({"ngl", "oobe", "adobegcclient", "slstore", "slcache"})
LICENSING_STEM_RE = re.compile(r"^(amtlib|adobe_licensing.*|agsservice.*|adobegcclient.*)$")

# Paths relative to drive_c that tuval must never write into.
VENDOR_WRITE_DENY: tuple[tuple[str, ...], ...] = (
    ("program files", "adobe"),
    ("program files (x86)", "adobe"),
    ("program files", "common files", "adobe"),
    ("program files (x86)", "common files", "adobe"),
    ("programdata", "adobe"),
)
WINDOWS_HOSTS_FILE = ("windows", "system32", "drivers", "etc", "hosts")
UNIX_HOSTS_FILE = ("etc", "hosts")

REGISTRY_ALLOWLIST = (
    "HKCU\\Software\\Wine",
    "HKCU\\Control Panel\\Desktop",
    "HKCU\\Environment",
    "HKLM\\System\\CurrentControlSet\\Hardware Profiles\\Current\\Software\\Fonts",
)

_DRIVE_ROOT_RE = re.compile(r"^(drive_[a-z]|[a-z]:)$")
_DLL_OVERRIDES_SUFFIX = "\\dlloverrides"
_ENVIRONMENT_KEY = "hkcu\\environment"
_WINEDLLOVERRIDES = "WINEDLLOVERRIDES"


@dataclass(frozen=True)
class Violation:
    rule: Rule
    message: str
    subject: str

    def __str__(self) -> str:
        return f"{self.rule.value} {self.subject}: {self.message}"


class ComplianceError(RuntimeError):
    def __init__(self, violations: list[Violation]) -> None:
        super().__init__("; ".join(str(violation) for violation in violations))
        self.violations = violations


def _parts(path: str | PurePath) -> tuple[str, ...]:
    """Lower-cased path segments; accepts both Windows and POSIX separators."""
    text = str(path).replace("\\", "/")
    return tuple(part.lower() for part in text.split("/") if part and part != ".")


def is_licensing_name(name: str) -> bool:
    """True if a bare file or DLL name (with or without extension) is a licensing component."""
    stem = PurePosixPath(name.replace("\\", "/")).stem.lower()
    return bool(LICENSING_STEM_RE.match(stem)) or bool(LICENSING_STEM_RE.match(name.lower()))


def is_licensing_path(path: str | PurePath) -> bool:
    parts = _parts(path)
    if not parts:
        return False
    return any(part in LICENSING_DIR_NAMES for part in parts) or is_licensing_name(parts[-1])


def _drive_relative(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    for index in range(len(parts) - 1, -1, -1):
        if _DRIVE_ROOT_RE.match(parts[index]):
            return parts[index + 1 :]
    return None


def _is_unix_absolute(path: str | PurePath) -> bool:
    return str(path).replace("\\", "/").startswith("/")


def check_write_target(path: str | PurePath) -> list[Violation]:
    """Violations that writing to ``path`` (host path or Windows path) would cause."""
    subject = str(path)
    parts = _parts(path)
    if ".." in parts:
        return [
            Violation(Rule.VENDOR_DIRECTORY_WRITE, "unnormalised path; resolve it first", subject)
        ]
    violations: list[Violation] = []
    relative = _drive_relative(parts)
    if relative is not None and any(relative[: len(deny)] == deny for deny in VENDOR_WRITE_DENY):
        violations.append(Violation(Rule.VENDOR_DIRECTORY_WRITE, "vendor-owned directory", subject))
    elif is_licensing_path(path):
        violations.append(Violation(Rule.VENDOR_DIRECTORY_WRITE, "licensing component", subject))
    if relative == WINDOWS_HOSTS_FILE or (
        relative is None and _is_unix_absolute(path) and parts == UNIX_HOSTS_FILE
    ):
        violations.append(Violation(Rule.HOSTS_FILE_WRITE, "hosts file", subject))
    return violations


def assert_write_allowed(path: str | PurePath) -> None:
    """Raise :class:`ComplianceError` if tuval must not write to ``path``."""
    violations = check_write_target(path)
    if violations:
        raise ComplianceError(violations)


def registry_key_allowed(key: str) -> bool:
    lowered = key.lower().rstrip("\\")
    return any(
        lowered == root.lower() or lowered.startswith(root.lower() + "\\")
        for root in REGISTRY_ALLOWLIST
    )


def check_registry_value(value: RegistryValue, subject: str) -> list[Violation]:
    """Violations that writing one registry value would cause."""
    violations: list[Violation] = []
    lowered_key = value.key.lower()
    if not registry_key_allowed(value.key):
        violations.append(Violation(Rule.REGISTRY_OUTSIDE_ALLOWLIST, "not allowlisted", subject))
    if lowered_key.endswith(_DLL_OVERRIDES_SUFFIX) and is_licensing_name(value.name):
        violations.append(Violation(Rule.LICENSING_DLL_OVERRIDE, "licensing DLL", subject))
    if lowered_key == _ENVIRONMENT_KEY and value.name.upper() == _WINEDLLOVERRIDES:
        violations.append(Violation(Rule.RAW_WINEDLLOVERRIDES, "use dll_overrides", subject))
    return violations


def _check_registry(profile: Profile) -> list[Violation]:
    return [
        violation
        for value in profile.wine.registry
        for violation in check_registry_value(value, f"{profile.id}: {value.key}\\{value.name}")
    ]


def check_profile(profile: Profile) -> list[Violation]:
    """All compliance violations in one profile; an empty list means it is acceptable."""
    violations: list[Violation] = []
    for dll in profile.wine.dll_overrides:
        if is_licensing_name(dll):
            violations.append(
                Violation(Rule.LICENSING_DLL_OVERRIDE, "licensing DLL", f"{profile.id}: {dll}")
            )
    violations.extend(_check_registry(profile))
    if any(name.upper() == _WINEDLLOVERRIDES for name in profile.wine.env):
        violations.append(
            Violation(Rule.RAW_WINEDLLOVERRIDES, "use dll_overrides", f"{profile.id}: env")
        )
    if is_licensing_path(profile.exe):
        violations.append(
            Violation(
                Rule.LICENSING_EXECUTABLE, "licensing component", f"{profile.id}: {profile.exe}"
            )
        )
    return violations
