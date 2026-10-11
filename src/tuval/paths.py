"""XDG-compliant locations.

Every function takes the environment as a mapping so callers (and tests) never depend
on the real process environment implicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath

APP_DIR_NAME = "tuval"
PREFIXES_DIR_NAME = "prefixes"


def is_absolute(value: str) -> bool:
    """Absolute on Linux (the target) or on the host running the tests."""
    return PurePosixPath(value).is_absolute() or Path(value).is_absolute()


def _home(env: Mapping[str, str]) -> Path:
    home = env.get("HOME")
    return Path(home) if home else Path.home()


def _xdg_dir(env: Mapping[str, str], variable: str, fallback: str) -> Path:
    value = env.get(variable, "")
    # The XDG spec says relative paths are invalid and must be ignored.
    if value and is_absolute(value):
        return Path(value)
    return _home(env) / fallback


def xdg_data_home(env: Mapping[str, str]) -> Path:
    return _xdg_dir(env, "XDG_DATA_HOME", ".local/share")


def xdg_config_home(env: Mapping[str, str]) -> Path:
    return _xdg_dir(env, "XDG_CONFIG_HOME", ".config")


def tuval_data_dir(env: Mapping[str, str]) -> Path:
    return xdg_data_home(env) / APP_DIR_NAME


def tuval_config_dir(env: Mapping[str, str]) -> Path:
    return xdg_config_home(env) / APP_DIR_NAME


def prefix_dir(name: str, env: Mapping[str, str]) -> Path:
    """Location of the Wine prefix called ``name`` (e.g. ``creative-cloud``)."""
    return tuval_data_dir(env) / PREFIXES_DIR_NAME / name
