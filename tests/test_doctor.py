from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.helpers import ADOBEWINE_WINE, healthy_host, make_probe, write
from tuval.doctor import (
    CheckResult,
    CheckStatus,
    SystemProbe,
    check_gpu,
    check_memory,
    check_platform,
    check_runtime,
    check_session,
    check_vulkan,
    check_winetricks,
    detect_gpu_vendors,
    has_failures,
    run_checks,
)
from tuval.runtime import CUSTOM_WINE_ENV

KIB_16_GB = 16_000_000
KIB_8_GB = 7_900_000
KIB_4_GB = 4_000_000


def add_card(root: Path, card: str, vendor: str) -> None:
    write(root.joinpath("sys", "class", "drm", card, "device", "vendor"), f"{vendor}\n")


def test_healthy_host_passes_every_check(tmp_path: Path) -> None:
    results = run_checks(healthy_host(tmp_path))
    assert [result.name for result in results] == [
        "platform",
        "display",
        "gpu",
        "vulkan",
        "winetricks",
        "runtime",
        "memory",
    ]
    assert {result.status for result in results} == {CheckStatus.OK}
    assert not has_failures(results)


def test_empty_host_fails(tmp_path: Path) -> None:
    results = run_checks(make_probe(tmp_path))
    assert has_failures(results)
    failed = {result.name for result in results if result.status is CheckStatus.FAIL}
    assert failed == {"gpu", "vulkan", "runtime"}


def test_non_linux_platform_fails(tmp_path: Path) -> None:
    result = check_platform(make_probe(tmp_path, platform="win32"))
    assert result.status is CheckStatus.FAIL
    assert "win32" in result.detail


@pytest.mark.parametrize(
    ("env", "status"),
    [
        ({"XDG_SESSION_TYPE": "x11"}, CheckStatus.OK),
        ({"DISPLAY": ":0"}, CheckStatus.OK),
        ({"XDG_SESSION_TYPE": "Wayland", "DISPLAY": ":1"}, CheckStatus.OK),
        ({"XDG_SESSION_TYPE": "wayland"}, CheckStatus.FAIL),
        ({"XDG_SESSION_TYPE": "tty"}, CheckStatus.WARN),
        ({}, CheckStatus.WARN),
    ],
)
def test_check_session(tmp_path: Path, env: dict[str, str], status: CheckStatus) -> None:
    assert check_session(make_probe(tmp_path, env=env)).status is status


def test_gpu_vendors_ignore_connectors_and_unknown_ids(tmp_path: Path) -> None:
    add_card(tmp_path, "card1", "0x10DE")
    add_card(tmp_path, "card0", "0x8086")
    add_card(tmp_path, "card2", "0x1234")
    add_card(tmp_path, "card0-HDMI-A-1", "0x1002")
    assert detect_gpu_vendors(make_probe(tmp_path)) == ["intel", "nvidia"]


def test_nvidia_with_proprietary_driver_is_ok(tmp_path: Path) -> None:
    probe = healthy_host(tmp_path)
    result = check_gpu(probe)
    assert result == CheckResult("gpu", CheckStatus.OK, "nvidia (NVIDIA proprietary driver)")


def test_nvidia_without_proprietary_driver_warns(tmp_path: Path) -> None:
    add_card(tmp_path, "card0", "0x10de")
    result = check_gpu(make_probe(tmp_path))
    assert result.status is CheckStatus.WARN
    assert "proprietary driver" in result.detail


def test_non_nvidia_gpu_asks_for_reports(tmp_path: Path) -> None:
    add_card(tmp_path, "card0", "0x1002")
    result = check_gpu(make_probe(tmp_path))
    assert result.status is CheckStatus.WARN
    assert result.detail.startswith("amd:")


def test_no_gpu_fails(tmp_path: Path) -> None:
    assert check_gpu(make_probe(tmp_path)).status is CheckStatus.FAIL


@pytest.mark.parametrize(
    "icd_dir",
    [
        ("usr", "share", "vulkan", "icd.d"),
        ("etc", "vulkan", "icd.d"),
        ("run", "opengl-driver", "share", "vulkan", "icd.d"),
    ],
)
def test_vulkan_icds_are_found(tmp_path: Path, icd_dir: tuple[str, ...]) -> None:
    write(tmp_path.joinpath(*icd_dir, "radeon_icd.x86_64.json"), "{}")
    result = check_vulkan(make_probe(tmp_path))
    assert result == CheckResult("vulkan", CheckStatus.OK, "radeon_icd.x86_64.json")


def test_vulkan_env_override_is_informational(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "VK_DRIVER_FILES": "/x/icd.json"}
    assert check_vulkan(make_probe(tmp_path, env=env)).status is CheckStatus.INFO


def test_vulkan_ignores_non_json_files(tmp_path: Path) -> None:
    write(tmp_path.joinpath("usr", "share", "vulkan", "icd.d", "README"), "")
    assert check_vulkan(make_probe(tmp_path)).status is CheckStatus.FAIL


def test_missing_winetricks_warns(tmp_path: Path) -> None:
    assert check_winetricks(make_probe(tmp_path)).status is CheckStatus.WARN


def system_wine_probe(root: Path, version_output: str) -> SystemProbe:
    wine = write(root / "usr" / "bin" / "wine")
    return make_probe(root, programs={"wine": str(wine)}, versions={wine: version_output})


@pytest.mark.parametrize(
    ("version_output", "status", "fragment"),
    [
        ("wine-11.18 (Staging)\n", CheckStatus.OK, "system: wine-11.18 (Staging)"),
        ("wine-11.18\n", CheckStatus.WARN, "Wine-Staging or AdobeWine recommended"),
        ("wine-11.2 (Staging)\n", CheckStatus.WARN, ">= 11.10 recommended"),
    ],
)
def test_check_runtime_grades_system_wine(
    tmp_path: Path, version_output: str, status: CheckStatus, fragment: str
) -> None:
    result = check_runtime(system_wine_probe(tmp_path, version_output))
    assert result.status is status
    assert fragment in result.detail


def test_adobewine_does_not_need_staging_marker(tmp_path: Path) -> None:
    wine = write(tmp_path.joinpath(*ADOBEWINE_WINE))
    probe = make_probe(tmp_path, versions={wine: "wine-11.18\n"})
    assert check_runtime(probe).status is CheckStatus.OK


def test_missing_runtime_explains_how_to_get_one(tmp_path: Path) -> None:
    result = check_runtime(make_probe(tmp_path))
    assert result.status is CheckStatus.FAIL
    assert "install AdobeWine" in result.detail


def test_runtime_problems_are_reported(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), CUSTOM_WINE_ENV: str(tmp_path / "missing")}
    result = check_runtime(make_probe(tmp_path, env=env))
    assert result.status is CheckStatus.FAIL
    assert "points to a missing file" in result.detail


@pytest.mark.parametrize(
    ("meminfo", "status"),
    [
        (f"MemTotal:       {KIB_4_GB} kB\n", CheckStatus.WARN),
        (f"MemTotal:       {KIB_8_GB} kB\n", CheckStatus.INFO),
        (f"MemTotal:       {KIB_16_GB} kB\n", CheckStatus.INFO),
        ("MemTotal:       32768000 kB\nMemFree: 1 kB\n", CheckStatus.OK),
        ("garbage\n", CheckStatus.WARN),
    ],
)
def test_check_memory(tmp_path: Path, meminfo: str, status: CheckStatus) -> None:
    write(tmp_path / "proc" / "meminfo", meminfo)
    assert check_memory(make_probe(tmp_path)).status is status


def test_unreadable_meminfo_warns(tmp_path: Path) -> None:
    assert check_memory(make_probe(tmp_path)).status is CheckStatus.WARN


def test_probe_read_text_returns_none_for_missing_files(tmp_path: Path) -> None:
    probe = replace(make_probe(tmp_path), root=tmp_path / "nowhere")
    assert probe.read_text("proc", "meminfo") is None
