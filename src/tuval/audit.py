"""Read-only audit of an existing Wine prefix.

Detects the usual ways licensing gets tampered with — whether by a "fix" script, a
crack, or an over-eager tutorial — so the user can restore a clean state. The audit
never modifies anything.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from tuval.compliance import VENDOR_WRITE_DENY, is_licensing_name, is_licensing_path
from tuval.log import get_logger

logger = get_logger("audit")

RENAME_SUFFIXES = (".disabled", ".bak", ".orig", ".old")
VENDOR_HOST_MARKER = "adobe"
MAX_SCANNED_FILES = 50_000
MAX_SHOWN_HOSTNAMES = 3
REGISTRY_FILES = ("user.reg", "system.reg")
PREFIX_HOSTS_FILE = ("drive_c", "windows", "system32", "drivers", "etc", "hosts")

_SECTION_RE = re.compile(r"^\[(.+?)\]")
_VALUE_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"=(.*)$')
_STRING_DATA_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*$')
_ESCAPE_RE = re.compile(r"\\(.)")
_DLL_OVERRIDES_SUFFIX = "\\dlloverrides"
_ENVIRONMENT_SECTION = "environment"
_WINEDLLOVERRIDES = "winedlloverrides"
# Top-level vendor directory whose per-application subfolders hold amtlib.dll et al.
_VENDOR_APPS_DIR = ("program files", "adobe")


class AuditRule(StrEnum):
    HOSTS_BLOCKS_VENDOR = "TUV101"
    LICENSING_COMPONENT_RENAMED = "TUV102"
    LICENSING_DLL_OVERRIDE = "TUV103"


class AuditError(RuntimeError):
    """The audit could not run (e.g. the path is not a Wine prefix)."""


@dataclass(frozen=True)
class Finding:
    rule: AuditRule
    message: str
    path: str

    def __str__(self) -> str:
        return f"{self.rule.value} {self.path}: {self.message}"


def _hostnames_blocking_vendor(text: str) -> list[str]:
    blocked: list[str] = []
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        blocked.extend(name for name in fields[1:] if VENDOR_HOST_MARKER in name.lower())
    return blocked


def audit_hosts_file(path: Path) -> list[Finding]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("cannot read %s: %s", path, exc)
        return []
    names = _hostnames_blocking_vendor(text)
    if not names:
        return []
    shown = ", ".join(names[:MAX_SHOWN_HOSTNAMES])
    if len(names) > MAX_SHOWN_HOSTNAMES:
        shown += ", ..."
    message = f"{len(names)} vendor hostname(s) redirected ({shown}); activation may be blocked"
    return [Finding(AuditRule.HOSTS_BLOCKS_VENDOR, message, str(path))]


def _child_ci(parent: Path, name: str) -> Path | None:
    """Case-insensitive child lookup — Wine prefixes preserve Windows casing."""
    if not parent.is_dir():
        return None
    for child in parent.iterdir():
        if child.name.lower() == name:
            return child
    return None


def _resolve_ci(root: Path, parts: tuple[str, ...]) -> Path | None:
    current: Path | None = root
    for part in parts:
        if current is None:
            return None
        current = _child_ci(current, part)
    return current


def _iter_files(root: Path) -> Iterator[Path]:
    scanned = 0
    for directory, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            scanned += 1
            if scanned > MAX_SCANNED_FILES:
                logger.warning("stopped scanning %s after %d files", root, MAX_SCANNED_FILES)
                return
            yield Path(directory) / name


def _renamed_licensing_original(path: Path, base: Path) -> str | None:
    """If ``path`` is a renamed licensing file, return its original relative name."""
    lowered = path.name.lower()
    for suffix in RENAME_SUFFIXES:
        if lowered.endswith(suffix):
            original = PurePosixPath(path.relative_to(base).as_posix()[: -len(suffix)])
            return str(original) if is_licensing_path(original) else None
    return None


def _scan_for_renames(base: Path, files: Iterator[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        original = _renamed_licensing_original(path, base)
        if original is not None:
            message = f"licensing component renamed (original: {original}); reinstall to restore"
            findings.append(Finding(AuditRule.LICENSING_COMPONENT_RENAMED, message, str(path)))
    return findings


def _app_dir_files(apps_dir: Path) -> Iterator[Path]:
    """Files directly inside each application folder (where amtlib.dll used to live)."""
    for app in sorted(apps_dir.iterdir()):
        if app.is_dir() and not app.is_symlink():
            yield from (child for child in sorted(app.iterdir()) if child.is_file())


def audit_renamed_components(drive_c: Path) -> list[Finding]:
    findings: list[Finding] = []
    for parts in VENDOR_WRITE_DENY:
        base = _resolve_ci(drive_c, parts)
        if base is None or not base.is_dir():
            continue
        if parts == _VENDOR_APPS_DIR:
            findings.extend(_scan_for_renames(base, _app_dir_files(base)))
        else:
            findings.extend(_scan_for_renames(base, _iter_files(base)))
    return findings


def _unescape(text: str) -> str:
    return _ESCAPE_RE.sub(lambda match: match.group(1), text)


def _dlls_in_override_string(value: str) -> list[str]:
    """DLL names in a WINEDLLOVERRIDES-style string such as ``a,b=n,b;c=d``."""
    names: list[str] = []
    for entry in value.split(";"):
        names.extend(name.strip() for name in entry.split("=", 1)[0].split(",") if name.strip())
    return names


def _registry_entries(path: Path) -> Iterator[tuple[str, str, str]]:
    """Yield ``(section, value_name, raw_data)`` from a Wine ``.reg`` file."""
    section = ""
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.rstrip("\r\n")
            header = _SECTION_RE.match(stripped)
            if header is not None:
                section = header.group(1).replace("\\\\", "\\")
                continue
            value = _VALUE_RE.match(stripped)
            if value is not None:
                yield section, _unescape(value.group(1)), value.group(2)


def _licensing_overrides(section: str, name: str, raw_data: str) -> list[str]:
    lowered = section.lower()
    if lowered.endswith(_DLL_OVERRIDES_SUFFIX):
        return [name] if is_licensing_name(name.lstrip("*")) else []
    if lowered == _ENVIRONMENT_SECTION and name.lower() == _WINEDLLOVERRIDES:
        data = _STRING_DATA_RE.match(raw_data)
        dlls = _dlls_in_override_string(_unescape(data.group(1))) if data else []
        return [dll for dll in dlls if is_licensing_name(dll.lstrip("*"))]
    return []


def audit_registry_file(path: Path) -> list[Finding]:
    if not path.is_file():
        return []
    findings: list[Finding] = []
    try:
        for section, name, raw_data in _registry_entries(path):
            for dll in _licensing_overrides(section, name, raw_data):
                message = f"DLL override for licensing component {dll!r} in [{section}]"
                findings.append(Finding(AuditRule.LICENSING_DLL_OVERRIDE, message, str(path)))
    except OSError as exc:
        logger.warning("cannot read %s: %s", path, exc)
    return findings


def audit_prefix(prefix: Path, *, host_root: Path | None = None) -> list[Finding]:
    """Audit ``prefix``; with ``host_root`` (normally ``/``) also check the host hosts file."""
    drive_c = prefix / "drive_c"
    if not drive_c.is_dir() and not (prefix / "system.reg").is_file():
        raise AuditError(f"{prefix} does not look like a Wine prefix (no drive_c or system.reg)")
    findings = audit_hosts_file(prefix.joinpath(*PREFIX_HOSTS_FILE))
    if host_root is not None:
        findings.extend(audit_hosts_file(host_root / "etc" / "hosts"))
    if drive_c.is_dir():
        findings.extend(audit_renamed_components(drive_c))
    for name in REGISTRY_FILES:
        findings.extend(audit_registry_file(prefix / name))
    return findings
