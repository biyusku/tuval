"""Discovery of Wine runtimes.

tuval does not build or ship Wine. It finds a runtime the user installed — an explicit
``TUVAL_WINE`` first, then AdobeWine (a Wine build carrying Creative Cloud fixes), then
the system Wine — and reports what it is.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tuval.log import get_logger
from tuval.paths import is_absolute, xdg_data_home
from tuval.version import Version

logger = get_logger("runtime")

# AdobeOnLinux documents Wine-Staging 11.10 as its minimum; see docs/LANDSCAPE.md.
RECOMMENDED_MIN_WINE = Version(11, 10)
WINE_VERSION_TIMEOUT_S = 15.0
CUSTOM_WINE_ENV = "TUVAL_WINE"
ADOBEWINE_HOME_ENV = "ADOBEWINE_HOME"
ADOBEWINE_SYSTEM_RUNTIME = ("opt", "adobewine", "runtime")
WINE_BINARY = ("bin", "wine")

_WINE_VERSION_RE = re.compile(r"^wine-(\d+)\.(\d+)(?:\.(\d+))?")

Which = Callable[[str], str | None]
VersionReader = Callable[[Path], str | None]


class RuntimeKind(StrEnum):
    CUSTOM = "custom"
    ADOBEWINE = "adobewine"
    SYSTEM = "system"


@dataclass(frozen=True)
class WineInfo:
    version: Version
    staging: bool
    raw: str


@dataclass(frozen=True)
class Runtime:
    kind: RuntimeKind
    wine: Path
    info: WineInfo | None
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None and self.info is not None

    @property
    def meets_recommendation(self) -> bool:
        return self.info is not None and self.info.version >= RECOMMENDED_MIN_WINE


def parse_wine_version(output: str) -> WineInfo | None:
    """Parse ``wine --version`` output such as ``wine-11.2 (Staging)``."""
    lines = output.strip().splitlines()
    if not lines:
        return None
    first = lines[0].strip()
    match = _WINE_VERSION_RE.match(first)
    if match is None:
        return None
    major, minor, patch = match.groups()
    version = Version(int(major), int(minor), int(patch) if patch else 0)
    return WineInfo(version, "staging" in first.lower(), first)


def read_wine_version(wine: Path) -> str | None:
    """Run ``wine --version``; never raises, returns ``None`` on any failure."""
    try:
        completed = subprocess.run(
            [str(wine), "--version"],
            capture_output=True,
            text=True,
            timeout=WINE_VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("%s --version failed: %s", wine, exc)
        return None
    if completed.returncode != 0:
        logger.debug("%s --version exited with %d", wine, completed.returncode)
        return None
    return completed.stdout


def candidate_paths(
    env: Mapping[str, str], *, root: Path, which: Which
) -> list[tuple[RuntimeKind, Path]]:
    """Runtime locations in priority order (``root`` is ``/`` outside tests)."""
    candidates: list[tuple[RuntimeKind, Path]] = []
    custom = env.get(CUSTOM_WINE_ENV, "")
    if custom:
        candidates.append((RuntimeKind.CUSTOM, Path(custom)))
    candidates.append(
        (RuntimeKind.ADOBEWINE, root.joinpath(*ADOBEWINE_SYSTEM_RUNTIME, *WINE_BINARY))
    )
    adobewine_home = env.get(ADOBEWINE_HOME_ENV, "")
    home = Path(adobewine_home) if adobewine_home and is_absolute(adobewine_home) else None
    home = home or xdg_data_home(env) / "adobewine"
    candidates.append((RuntimeKind.ADOBEWINE, home.joinpath("runtime", *WINE_BINARY)))
    system = which("wine")
    if system:
        candidates.append((RuntimeKind.SYSTEM, Path(system)))
    return candidates


def discover_runtimes(
    env: Mapping[str, str],
    *,
    root: Path = Path("/"),
    which: Which = shutil.which,
    reader: VersionReader = read_wine_version,
) -> list[Runtime]:
    """Every runtime that exists, in priority order, with its parsed version."""
    runtimes: list[Runtime] = []
    seen: set[Path] = set()
    for kind, wine in candidate_paths(env, root=root, which=which):
        if wine in seen:
            continue
        seen.add(wine)
        if not wine.is_file():
            if kind is RuntimeKind.CUSTOM:
                runtimes.append(
                    Runtime(kind, wine, None, f"{CUSTOM_WINE_ENV} points to a missing file")
                )
            continue
        output = reader(wine)
        info = parse_wine_version(output) if output else None
        problem = None if info else "could not determine the Wine version"
        runtimes.append(Runtime(kind, wine, info, problem))
    return runtimes


def select_runtime(runtimes: list[Runtime]) -> Runtime | None:
    """The highest-priority usable runtime."""
    return next((runtime for runtime in runtimes if runtime.usable), None)
