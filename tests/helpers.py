"""Shared test helpers: fake Linux hosts and Wine prefixes built under ``tmp_path``.

The tool targets Linux but the suite must pass anywhere, so nothing here touches the
real system: every probe is rooted in a temporary directory.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tuval.doctor import SystemProbe
from tuval.setup import Command

ADOBEWINE_WINE = ("opt", "adobewine", "runtime", "bin", "wine")
EMPTY_REGISTRY = "WINE REGISTRY Version 2\n"
WINETRICKS = "/usr/bin/winetricks"
STAGING_VERSION = "wine-11.18 (Staging)\n"
FAILED = 1


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_probe(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
    programs: Mapping[str, str] | None = None,
    versions: Mapping[Path, str] | None = None,
    platform: str = "linux",
) -> SystemProbe:
    """A probe whose filesystem is ``root``; ``versions`` maps wine paths to ``--version``."""
    found = dict(programs or {})
    outputs = dict(versions or {})
    return SystemProbe(
        root=root,
        env=dict(env if env is not None else {"HOME": str(root / "home")}),
        which=found.get,
        platform=platform,
        version_reader=outputs.get,
    )


def healthy_host(root: Path) -> SystemProbe:
    """A machine on which every doctor check passes."""
    write(root.joinpath("sys", "class", "drm", "card0", "device", "vendor"), "0x10de\n")
    write(root.joinpath("proc", "driver", "nvidia", "version"), "NVRM version: 580.95\n")
    write(root.joinpath("usr", "share", "vulkan", "icd.d", "nvidia_icd.json"), "{}\n")
    write(root.joinpath("proc", "meminfo"), "MemTotal:       32768000 kB\n")
    wine = write(root.joinpath(*ADOBEWINE_WINE))
    return make_probe(
        root,
        env={"HOME": str(root / "home"), "XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"},
        programs={"winetricks": WINETRICKS},
        versions={wine: STAGING_VERSION},
    )


def setup_host(
    root: Path,
    *,
    extra_env: Mapping[str, str] | None = None,
    programs: Mapping[str, str] | None = None,
    version: str = STAGING_VERSION,
) -> SystemProbe:
    """A machine ready for ``tuval setup``: AdobeWine with its wineserver, and winetricks."""
    wine = write(root.joinpath(*ADOBEWINE_WINE))
    write(wine.with_name("wineserver"))
    return make_probe(
        root,
        env={"HOME": str(root / "home"), **(extra_env or {})},
        programs={"winetricks": WINETRICKS} if programs is None else programs,
        versions={wine: version},
    )


def make_prefix(path: Path) -> Path:
    """A minimal, clean Wine prefix."""
    (path / "drive_c" / "windows" / "system32").mkdir(parents=True)
    write(path / "system.reg", EMPTY_REGISTRY)
    write(path / "user.reg", EMPTY_REGISTRY)
    return path


class RecordingRunner:
    """Stands in for Wine: records every command and creates the prefix on ``wineboot``."""

    def __init__(self, *, failing: str | None = None, creates_prefix: bool = True) -> None:
        self.commands: list[Command] = []
        self.failing = failing
        self.creates_prefix = creates_prefix

    def __call__(self, command: Command) -> int:
        self.commands.append(command)
        if self.creates_prefix and "wineboot" in command.argv:
            prefix = Path(command.env["WINEPREFIX"])
            if not prefix.exists():
                make_prefix(prefix)
        return FAILED if self.failing in command.argv else 0


def refuse_commands(command: Command) -> int:
    """Runner for tests that must not execute anything."""
    raise AssertionError(f"unexpected command: {command.argv}")
