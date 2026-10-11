from __future__ import annotations

from pathlib import Path

from tuval.paths import (
    is_absolute,
    prefix_dir,
    tuval_config_dir,
    tuval_data_dir,
    xdg_config_home,
    xdg_data_home,
)


def test_is_absolute_accepts_posix_paths_on_any_host() -> None:
    assert is_absolute("/home/user")
    assert not is_absolute("relative/dir")
    assert not is_absolute("")


def test_xdg_defaults_follow_home(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path)}
    assert xdg_data_home(env) == tmp_path / ".local/share"
    assert xdg_config_home(env) == tmp_path / ".config"


def test_absolute_xdg_values_win() -> None:
    env = {"HOME": "/home/u", "XDG_DATA_HOME": "/data", "XDG_CONFIG_HOME": "/conf"}
    assert tuval_data_dir(env) == Path("/data") / "tuval"
    assert tuval_config_dir(env) == Path("/conf") / "tuval"


def test_relative_xdg_values_are_ignored_per_spec(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "XDG_DATA_HOME": "relative/data"}
    assert xdg_data_home(env) == tmp_path / ".local/share"


def test_prefix_dir_lives_under_data_dir() -> None:
    env = {"HOME": "/home/u", "XDG_DATA_HOME": "/data"}
    assert prefix_dir("creative-cloud", env) == Path("/data/tuval/prefixes/creative-cloud")
