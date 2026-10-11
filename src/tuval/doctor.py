"""``tuval doctor`` — is this machine ready?

Every probe reads through an injectable :class:`SystemProbe` so the checks are testable
on any host. Nothing here modifies the system.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from tuval.runtime import (
    RECOMMENDED_MIN_WINE,
    RuntimeKind,
    VersionReader,
    Which,
    discover_runtimes,
    read_wine_version,
    select_runtime,
)

PCI_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
VULKAN_ICD_DIRS = (
    ("usr", "share", "vulkan", "icd.d"),
    ("etc", "vulkan", "icd.d"),
    ("run", "opengl-driver", "share", "vulkan", "icd.d"),  # NixOS
)
VULKAN_ICD_ENV = ("VK_DRIVER_FILES", "VK_ICD_FILENAMES")
NVIDIA_DRIVER_VERSION_FILE = ("proc", "driver", "nvidia", "version")
MEMINFO_FILE = ("proc", "meminfo")
KIB_PER_GIB = 1024 * 1024
LOW_RAM_GIB = 7.5  # an "8 GB" machine reports slightly less usable memory
MODEST_RAM_GIB = 20.0  # covers "16 GB" machines

_CARD_RE = re.compile(r"^card\d+$")
_MEMTOTAL_RE = re.compile(r"^MemTotal:\s+(\d+)\s+kB", re.MULTILINE)


class CheckStatus(StrEnum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


def _real_env() -> Mapping[str, str]:
    return dict(os.environ)


@dataclass(frozen=True)
class SystemProbe:
    root: Path = Path("/")
    env: Mapping[str, str] = field(default_factory=_real_env)
    which: Which = shutil.which
    platform: str = sys.platform
    version_reader: VersionReader = read_wine_version

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def read_text(self, *parts: str) -> str | None:
        try:
            return self.path(*parts).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def check_platform(probe: SystemProbe) -> CheckResult:
    if probe.platform.startswith("linux"):
        return CheckResult("platform", CheckStatus.OK, "Linux")
    return CheckResult("platform", CheckStatus.FAIL, f"tuval targets Linux, found {probe.platform}")


def check_session(probe: SystemProbe) -> CheckResult:
    session = probe.env.get("XDG_SESSION_TYPE", "").lower()
    has_x = bool(probe.env.get("DISPLAY"))
    if session == "x11" or (not session and has_x):
        return CheckResult("display", CheckStatus.OK, "X11")
    if session == "wayland" and has_x:
        return CheckResult("display", CheckStatus.OK, "Wayland with XWayland (Wine uses X11)")
    if session == "wayland":
        return CheckResult("display", CheckStatus.FAIL, "Wayland without XWayland (DISPLAY unset)")
    return CheckResult("display", CheckStatus.WARN, "no graphical session detected")


def detect_gpu_vendors(probe: SystemProbe) -> list[str]:
    drm = probe.path("sys", "class", "drm")
    if not drm.is_dir():
        return []
    vendors: list[str] = []
    for card in sorted(drm.iterdir()):
        if not _CARD_RE.match(card.name):
            continue
        raw = probe.read_text("sys", "class", "drm", card.name, "device", "vendor")
        vendor = PCI_VENDORS.get(raw.strip().lower()) if raw else None
        if vendor and vendor not in vendors:
            vendors.append(vendor)
    return vendors


def check_gpu(probe: SystemProbe) -> CheckResult:
    vendors = detect_gpu_vendors(probe)
    if not vendors:
        return CheckResult("gpu", CheckStatus.FAIL, "no GPU found under /sys/class/drm")
    listed = ", ".join(vendors)
    if "nvidia" in vendors:
        if probe.path(*NVIDIA_DRIVER_VERSION_FILE).is_file():
            return CheckResult("gpu", CheckStatus.OK, f"{listed} (NVIDIA proprietary driver)")
        detail = f"{listed}: NVIDIA without the proprietary driver; CUDA/NVENC features unavailable"
        return CheckResult("gpu", CheckStatus.WARN, detail)
    detail = f"{listed}: almost all public test data is from NVIDIA; please report your results"
    return CheckResult("gpu", CheckStatus.WARN, detail)


def check_vulkan(probe: SystemProbe) -> CheckResult:
    overridden = [name for name in VULKAN_ICD_ENV if probe.env.get(name)]
    if overridden:
        return CheckResult("vulkan", CheckStatus.INFO, f"ICDs set via {', '.join(overridden)}")
    icds: list[str] = []
    for parts in VULKAN_ICD_DIRS:
        directory = probe.path(*parts)
        if directory.is_dir():
            icds.extend(sorted(item.name for item in directory.glob("*.json")))
    if not icds:
        return CheckResult("vulkan", CheckStatus.FAIL, "no Vulkan driver (DXVK/vkd3d need one)")
    return CheckResult("vulkan", CheckStatus.OK, ", ".join(icds))


def check_winetricks(probe: SystemProbe) -> CheckResult:
    found = probe.which("winetricks")
    if found:
        return CheckResult("winetricks", CheckStatus.OK, found)
    return CheckResult("winetricks", CheckStatus.WARN, "not on PATH; needed for fonts and DLLs")


def check_runtime(probe: SystemProbe) -> CheckResult:
    runtimes = discover_runtimes(
        probe.env, root=probe.root, which=probe.which, reader=probe.version_reader
    )
    runtime = select_runtime(runtimes)
    if runtime is None or runtime.info is None:
        problems = "; ".join(f"{r.wine}: {r.problem}" for r in runtimes if r.problem)
        detail = (
            problems or f"none found; install AdobeWine or Wine-Staging >= {RECOMMENDED_MIN_WINE}"
        )
        return CheckResult("runtime", CheckStatus.FAIL, detail)
    summary = f"{runtime.kind}: {runtime.info.raw} ({runtime.wine})"
    if not runtime.meets_recommendation:
        return CheckResult(
            "runtime", CheckStatus.WARN, f"{summary}; >= {RECOMMENDED_MIN_WINE} recommended"
        )
    if runtime.kind is RuntimeKind.SYSTEM and not runtime.info.staging:
        return CheckResult(
            "runtime", CheckStatus.WARN, f"{summary}; Wine-Staging or AdobeWine recommended"
        )
    return CheckResult("runtime", CheckStatus.OK, summary)


def check_memory(probe: SystemProbe) -> CheckResult:
    meminfo = probe.read_text(*MEMINFO_FILE)
    match = _MEMTOTAL_RE.search(meminfo or "")
    if match is None:
        return CheckResult("memory", CheckStatus.WARN, "could not read /proc/meminfo")
    gib = int(match.group(1)) / KIB_PER_GIB
    if gib < LOW_RAM_GIB:
        return CheckResult(
            "memory", CheckStatus.WARN, f"{gib:.1f} GiB; large documents will struggle"
        )
    if gib < MODEST_RAM_GIB:
        detail = f"{gib:.1f} GiB; Illustrator memory pressure reported on 16 GB machines"
        return CheckResult("memory", CheckStatus.INFO, detail)
    return CheckResult("memory", CheckStatus.OK, f"{gib:.1f} GiB")


CHECKS: tuple[Callable[[SystemProbe], CheckResult], ...] = (
    check_platform,
    check_session,
    check_gpu,
    check_vulkan,
    check_winetricks,
    check_runtime,
    check_memory,
)


def run_checks(probe: SystemProbe) -> list[CheckResult]:
    return [check(probe) for check in CHECKS]


def has_failures(results: list[CheckResult]) -> bool:
    return any(result.status is CheckStatus.FAIL for result in results)
