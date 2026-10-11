"""Declarative application profiles.

A profile is a small TOML file describing *how* to run one application inside a shared
Wine prefix and — just as important — *how well* it is known to work, with evidence.
Parsing is strict: unknown keys, wrong types and dishonest status claims are errors.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import TypeVar
from urllib.parse import urlsplit

from tuval.version import Version

SCHEMA_VERSION = 1
DWORD_MAX = 0xFFFFFFFF

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
WINETRICKS_VERB_RE = re.compile(r"^[a-z0-9_]+$")
DLL_NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_.\-]*$")
ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MIME_RE = re.compile(r"^[a-z]+/[a-z0-9.+\-]+$")
REGISTRY_KEY_RE = re.compile(r"^(HKCU|HKLM)(\\[^\\]+)+$")

WINDOWS_VERSIONS = frozenset({"win7", "win8", "win81", "win10", "win11"})
DLL_OVERRIDE_VALUES = frozenset(
    {"native", "builtin", "native,builtin", "builtin,native", "disabled", ""}
)
GPU_VENDORS = frozenset({"nvidia", "amd", "intel"})

T = TypeVar("T")
E = TypeVar("E", bound=StrEnum)


class ProfileError(ValueError):
    """A profile file is malformed or makes a claim the schema does not allow."""


class Tier(StrEnum):
    PLATINUM = "platinum"
    GOLD = "gold"
    SILVER = "silver"
    BRONZE = "bronze"
    BROKEN = "broken"
    UNTESTED = "untested"


# Tiers that may only be claimed after the tuval project itself verified the app.
SELF_VERIFIED_TIERS = frozenset({Tier.PLATINUM, Tier.GOLD})


class InstallMethod(StrEnum):
    CREATIVE_CLOUD = "creative-cloud"
    STANDALONE_INSTALLER = "standalone-installer"


class RegistryType(StrEnum):
    REG_SZ = "REG_SZ"
    REG_DWORD = "REG_DWORD"


@dataclass(frozen=True)
class RegistryValue:
    key: str
    name: str
    kind: RegistryType
    data: str | int
    why: str


@dataclass(frozen=True)
class WineSettings:
    windows_version: str | None = None
    winetricks: tuple[str, ...] = ()
    dll_overrides: Mapping[str, str] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    registry: tuple[RegistryValue, ...] = ()


@dataclass(frozen=True)
class Status:
    tier: Tier
    verified_by_us: bool
    tested_gpus: tuple[str, ...]
    evidence: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class KnownIssue:
    summary: str
    source: str


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    vendor: str
    prefix: str
    installed_via: InstallMethod
    exe: str
    min_wine: Version
    status: Status
    wine: WineSettings = field(default_factory=WineSettings)
    requires: tuple[str, ...] = ()
    mime_types: tuple[str, ...] = ()
    known_issues: tuple[KnownIssue, ...] = ()
    source: str = "<memory>"


class _Table:
    """Typed, strict view of one TOML table. Call :meth:`finish` to reject unknown keys."""

    def __init__(self, data: Mapping[str, object], where: str) -> None:
        self._data = data
        self.where = where
        self._used: set[str] = set()

    def error(self, message: str) -> ProfileError:
        return ProfileError(f"{self.where}: {message}")

    def _get(self, key: str, expected: type[T]) -> T | None:
        self._used.add(key)
        if key not in self._data:
            return None
        value = self._data[key]
        # bool is a subclass of int; never accept one for the other.
        if isinstance(value, bool) and expected is not bool:
            raise self.error(f"{key!r} must be {expected.__name__}, got bool")
        if not isinstance(value, expected):
            raise self.error(f"{key!r} must be {expected.__name__}, got {type(value).__name__}")
        return value

    def _require(self, key: str, expected: type[T]) -> T:
        value = self._get(key, expected)
        if value is None:
            raise self.error(f"missing required key {key!r}")
        return value

    def req_str(self, key: str, *, allow_empty: bool = False) -> str:
        value = self._require(key, str)
        if not allow_empty and not value.strip():
            raise self.error(f"{key!r} must not be empty")
        return value

    def opt_str(self, key: str) -> str | None:
        return self._get(key, str)

    def req_int(self, key: str) -> int:
        return self._require(key, int)

    def req_bool(self, key: str) -> bool:
        return self._require(key, bool)

    def str_list(self, key: str) -> tuple[str, ...]:
        items = self._get(key, list) or []
        if not all(isinstance(item, str) for item in items):
            raise self.error(f"every item of {key!r} must be a string")
        return tuple(str(item) for item in items)

    def str_map(self, key: str) -> dict[str, str]:
        table = self.table(key)
        result = {name: table.req_str(name, allow_empty=True) for name in table.names()}
        table.finish()
        return result

    def table(self, key: str, *, required: bool = False) -> _Table:
        data = self._require(key, dict) if required else self._get(key, dict) or {}
        return _Table(data, f"{self.where}.{key}")

    def table_list(self, key: str) -> list[_Table]:
        items = self._get(key, list) or []
        tables: list[_Table] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise self.error(f"every item of {key!r} must be a table")
            tables.append(_Table(item, f"{self.where}.{key}[{index}]"))
        return tables

    def names(self) -> list[str]:
        return list(self._data)

    def finish(self) -> None:
        unknown = sorted(set(self._data) - self._used)
        if unknown:
            raise self.error(f"unknown key(s): {', '.join(unknown)}")


def is_https_url(text: str) -> bool:
    parts = urlsplit(text)
    return parts.scheme == "https" and bool(parts.netloc) and not any(c.isspace() for c in text)


def _match(table: _Table, key: str, pattern: re.Pattern[str]) -> str:
    value = table.req_str(key)
    if not pattern.match(value):
        raise table.error(f"{key!r} value {value!r} does not match {pattern.pattern}")
    return value


def _match_list(table: _Table, key: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    values = table.str_list(key)
    for value in values:
        if not pattern.match(value):
            raise table.error(f"{key!r} item {value!r} does not match {pattern.pattern}")
    return values


def _enum(table: _Table, key: str, enum_type: type[E]) -> E:
    raw = table.req_str(key)
    try:
        return enum_type(raw)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_type)
        raise table.error(f"{key!r} must be one of: {allowed}") from None


def _parse_version(table: _Table, key: str) -> Version:
    raw = table.req_str(key)
    try:
        return Version.parse(raw)
    except ValueError:
        raise table.error(f"{key!r} must look like 11.10, got {raw!r}") from None


def _parse_exe(table: _Table) -> str:
    exe = table.req_str("exe")
    path = PurePosixPath(exe)
    if "\\" in exe or ":" in exe or path.is_absolute() or ".." in path.parts:
        raise table.error("'exe' must be a relative POSIX path under drive_c without '..'")
    if not exe.lower().endswith(".exe"):
        raise table.error("'exe' must end in .exe")
    return exe


def _parse_registry(table: _Table) -> RegistryValue:
    key = table.req_str("key")
    if not REGISTRY_KEY_RE.match(key):
        raise table.error(f"registry key {key!r} must start with HKCU\\ or HKLM\\")
    kind = _enum(table, "type", RegistryType)
    data: str | int
    if kind is RegistryType.REG_DWORD:
        data = table.req_int("data")
        if not 0 <= data <= DWORD_MAX:
            raise table.error("REG_DWORD data must fit in 32 bits")
    else:
        data = table.req_str("data", allow_empty=True)
    value = RegistryValue(key, table.req_str("name"), kind, data, table.req_str("why"))
    table.finish()
    return value


def _parse_wine(table: _Table) -> WineSettings:
    windows_version = table.opt_str("windows_version")
    if windows_version is not None and windows_version not in WINDOWS_VERSIONS:
        raise table.error(f"windows_version must be one of {sorted(WINDOWS_VERSIONS)}")
    overrides = table.str_map("dll_overrides")
    for dll, mode in overrides.items():
        if not DLL_NAME_RE.match(dll) or mode not in DLL_OVERRIDE_VALUES:
            raise table.error(f"invalid dll override {dll!r} = {mode!r}")
    env = table.str_map("env")
    for name in env:
        if not ENV_KEY_RE.match(name):
            raise table.error(f"invalid environment variable name {name!r}")
    settings = WineSettings(
        windows_version=windows_version,
        winetricks=_match_list(table, "winetricks", WINETRICKS_VERB_RE),
        dll_overrides=overrides,
        env=env,
        registry=tuple(_parse_registry(item) for item in table.table_list("registry")),
    )
    table.finish()
    return settings


def _check_honesty(table: _Table, tier: Tier, verified: bool, evidence: tuple[str, ...]) -> None:
    if tier in SELF_VERIFIED_TIERS and not verified:
        raise table.error(f"tier {tier} requires verified_by_us = true")
    if tier is Tier.UNTESTED and verified:
        raise table.error("an untested profile cannot be verified_by_us")
    if tier is not Tier.UNTESTED and not evidence:
        raise table.error(f"tier {tier} needs at least one evidence URL")


def _parse_status(table: _Table) -> Status:
    tier = _enum(table, "tier", Tier)
    verified = table.req_bool("verified_by_us")
    gpus = table.str_list("tested_gpus")
    unknown_gpus = set(gpus) - GPU_VENDORS
    if unknown_gpus:
        raise table.error(f"unknown GPU vendor(s): {sorted(unknown_gpus)}")
    evidence = table.str_list("evidence")
    for url in evidence:
        if not is_https_url(url):
            raise table.error(f"evidence must be https URLs, got {url!r}")
    notes = table.opt_str("notes") or ""
    table.finish()
    _check_honesty(table, tier, verified, evidence)
    return Status(tier, verified, gpus, evidence, notes)


def _parse_issue(table: _Table) -> KnownIssue:
    issue = KnownIssue(table.req_str("summary"), table.req_str("source"))
    if not is_https_url(issue.source):
        raise table.error(f"known issue source must be an https URL, got {issue.source!r}")
    table.finish()
    return issue


def _parse_root(root: _Table, source: str) -> Profile:
    if root.req_int("schema") != SCHEMA_VERSION:
        raise root.error(f"unsupported schema; this tuval understands schema {SCHEMA_VERSION}")
    profile = Profile(
        id=_match(root, "id", ID_RE),
        name=root.req_str("name"),
        vendor=root.req_str("vendor"),
        prefix=_match(root, "prefix", ID_RE),
        installed_via=_enum(root, "installed_via", InstallMethod),
        exe=_parse_exe(root),
        min_wine=_parse_version(root, "min_wine"),
        requires=_match_list(root, "requires", ID_RE),
        mime_types=_match_list(root, "mime_types", MIME_RE),
        wine=_parse_wine(root.table("wine")),
        status=_parse_status(root.table("status", required=True)),
        known_issues=tuple(_parse_issue(item) for item in root.table_list("known_issues")),
        source=source,
    )
    root.finish()
    return profile


def parse_profile(text: str, source: str = "<memory>") -> Profile:
    """Parse and validate one profile document."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{source}: invalid TOML: {exc}") from exc
    return _parse_root(_Table(data, source), source)


def _check_file_name(profile: Profile, file_name: str) -> None:
    stem = PurePosixPath(file_name).stem
    if profile.id != stem:
        raise ProfileError(f"{profile.source}: id {profile.id!r} must match file name {stem!r}")


def load_profile_file(path: Path) -> Profile:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"{path}: cannot read: {exc}") from exc
    profile = parse_profile(text, str(path))
    _check_file_name(profile, path.name)
    return profile


def index_profiles(profiles: Iterable[Profile]) -> dict[str, Profile]:
    """Index profiles by id, rejecting duplicates and dangling ``requires``."""
    index: dict[str, Profile] = {}
    for profile in profiles:
        if profile.id in index:
            raise ProfileError(f"{profile.source}: duplicate profile id {profile.id!r}")
        index[profile.id] = profile
    for profile in index.values():
        for dependency in profile.requires:
            if dependency == profile.id or dependency not in index:
                raise ProfileError(f"{profile.source}: invalid requirement {dependency!r}")
    return index


def load_bundled_profiles() -> dict[str, Profile]:
    """Load the profiles shipped inside the package."""
    directory = resources.files("tuval") / "data" / "profiles"
    profiles: list[Profile] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".toml"):
            continue
        profile = parse_profile(entry.read_text(encoding="utf-8"), f"<bundled>/{entry.name}")
        _check_file_name(profile, entry.name)
        profiles.append(profile)
    return index_profiles(profiles)
