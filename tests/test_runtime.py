from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers import ADOBEWINE_WINE, write
from tuval.runtime import (
    CUSTOM_WINE_ENV,
    Runtime,
    RuntimeKind,
    WineInfo,
    candidate_paths,
    discover_runtimes,
    parse_wine_version,
    read_wine_version,
    select_runtime,
)
from tuval.version import Version

STAGING_OUTPUT = "wine-11.18 (Staging)\n"


def no_programs(_name: str) -> str | None:
    return None


def no_version(_wine: Path) -> str | None:
    return None


@pytest.mark.parametrize(
    ("output", "version", "staging"),
    [
        ("wine-11.2 (Staging)\n", Version(11, 2), True),
        ("wine-11.18\n", Version(11, 18), False),
        ("wine-10.0.1\n", Version(10, 0, 1), False),
        ("wine-11.18-42-gabc123 (Staging)\nextra line\n", Version(11, 18), True),
    ],
)
def test_parse_wine_version(output: str, version: Version, staging: bool) -> None:
    info = parse_wine_version(output)
    assert info is not None
    assert info.version == version
    assert info.staging is staging
    assert info.raw == output.splitlines()[0]


@pytest.mark.parametrize("output", ["", "  \n", "Wine 11.2", "wine-eleven", "002c:err:..."])
def test_parse_wine_version_rejects_other_output(output: str) -> None:
    assert parse_wine_version(output) is None


def test_candidate_paths_are_in_priority_order(tmp_path: Path) -> None:
    env = {"HOME": "/home/u", CUSTOM_WINE_ENV: "/custom/wine", "ADOBEWINE_HOME": "/aw"}
    candidates = candidate_paths(env, root=tmp_path, which={"wine": "/usr/bin/wine"}.get)
    assert candidates == [
        (RuntimeKind.CUSTOM, Path("/custom/wine")),
        (RuntimeKind.ADOBEWINE, tmp_path.joinpath(*ADOBEWINE_WINE)),
        (RuntimeKind.ADOBEWINE, Path("/aw/runtime/bin/wine")),
        (RuntimeKind.SYSTEM, Path("/usr/bin/wine")),
    ]


def test_relative_adobewine_home_falls_back_to_xdg(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "ADOBEWINE_HOME": "relative/aw"}
    candidates = candidate_paths(env, root=tmp_path, which=no_programs)
    expected = tmp_path / ".local/share/adobewine/runtime/bin/wine"
    assert [path for _kind, path in candidates] == [tmp_path.joinpath(*ADOBEWINE_WINE), expected]


def test_adobewine_runtime_is_discovered(tmp_path: Path) -> None:
    wine = write(tmp_path.joinpath(*ADOBEWINE_WINE))
    runtimes = discover_runtimes(
        {"HOME": str(tmp_path / "home")},
        root=tmp_path,
        which=no_programs,
        reader={wine: STAGING_OUTPUT}.get,
    )
    assert runtimes == [
        Runtime(
            RuntimeKind.ADOBEWINE, wine, WineInfo(Version(11, 18), True, STAGING_OUTPUT.strip())
        )
    ]
    assert select_runtime(runtimes) == runtimes[0]


def test_missing_custom_runtime_is_reported_but_not_selected(tmp_path: Path) -> None:
    system = write(tmp_path / "usr" / "bin" / "wine")
    env = {"HOME": str(tmp_path / "home"), CUSTOM_WINE_ENV: str(tmp_path / "nope" / "wine")}
    runtimes = discover_runtimes(
        env,
        root=tmp_path,
        which={"wine": str(system)}.get,
        reader={system: STAGING_OUTPUT}.get,
    )
    custom, chosen = runtimes
    assert custom.kind is RuntimeKind.CUSTOM
    assert custom.problem == f"{CUSTOM_WINE_ENV} points to a missing file"
    assert not custom.usable
    assert select_runtime(runtimes) is chosen
    assert chosen.kind is RuntimeKind.SYSTEM


def test_missing_non_custom_candidates_are_skipped(tmp_path: Path) -> None:
    runtimes = discover_runtimes(
        {"HOME": str(tmp_path)}, root=tmp_path, which=no_programs, reader=no_version
    )
    assert runtimes == []
    assert select_runtime(runtimes) is None


def test_same_binary_is_listed_once(tmp_path: Path) -> None:
    wine = write(tmp_path.joinpath(*ADOBEWINE_WINE))
    runtimes = discover_runtimes(
        {"HOME": str(tmp_path / "home"), CUSTOM_WINE_ENV: str(wine)},
        root=tmp_path,
        which={"wine": str(wine)}.get,
        reader={wine: STAGING_OUTPUT}.get,
    )
    assert [runtime.kind for runtime in runtimes] == [RuntimeKind.CUSTOM]


@pytest.mark.parametrize("output", [None, "", "not wine at all\n"])
def test_unreadable_version_makes_runtime_unusable(tmp_path: Path, output: str | None) -> None:
    wine = write(tmp_path.joinpath(*ADOBEWINE_WINE))

    def reader(_path: Path) -> str | None:
        return output

    [runtime] = discover_runtimes(
        {"HOME": str(tmp_path / "home")}, root=tmp_path, which=no_programs, reader=reader
    )
    assert runtime.wine == wine
    assert runtime.problem == "could not determine the Wine version"
    assert not runtime.usable
    assert select_runtime([runtime]) is None


@pytest.mark.parametrize(
    ("version", "expected"),
    [(Version(11, 9), False), (Version(11, 10), True), (Version(12, 0), True)],
)
def test_meets_recommendation(version: Version, expected: bool) -> None:
    runtime = Runtime(RuntimeKind.SYSTEM, Path("/usr/bin/wine"), WineInfo(version, True, "wine"))
    assert runtime.meets_recommendation is expected


def test_runtime_without_info_does_not_meet_recommendation() -> None:
    runtime = Runtime(RuntimeKind.CUSTOM, Path("/x/wine"), None, "missing")
    assert not runtime.meets_recommendation


def test_read_wine_version_returns_stdout() -> None:
    # Any binary that understands --version exercises the real subprocess path.
    output = read_wine_version(Path(sys.executable))
    assert output is not None
    assert output.startswith("Python ")


def test_read_wine_version_handles_missing_binary(tmp_path: Path) -> None:
    assert read_wine_version(tmp_path / "missing-wine") is None


def test_read_wine_version_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="wine", timeout=0)

    monkeypatch.setattr(subprocess, "run", hang)
    assert read_wine_version(Path("/usr/bin/wine")) is None


def test_read_wine_version_handles_failure_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["wine"], returncode=1, stdout="wine-11.18\n")

    monkeypatch.setattr(subprocess, "run", fail)
    assert read_wine_version(Path("/usr/bin/wine")) is None
