from __future__ import annotations

from dataclasses import replace

import pytest

from tuval.compliance import (
    RULE_DESCRIPTIONS,
    ComplianceError,
    Rule,
    Violation,
    assert_write_allowed,
    check_profile,
    check_registry_value,
    check_write_target,
    is_licensing_name,
    is_licensing_path,
    registry_key_allowed,
)
from tuval.profiles import (
    InstallMethod,
    Profile,
    RegistryType,
    RegistryValue,
    Status,
    Tier,
    WineSettings,
)
from tuval.version import Version

BASE = Profile(
    id="demo",
    name="Demo",
    vendor="Example",
    prefix="creative-cloud",
    installed_via=InstallMethod.CREATIVE_CLOUD,
    exe="Program Files/Adobe/Adobe Photoshop 2026/Photoshop.exe",
    min_wine=Version(11, 10),
    status=Status(Tier.UNTESTED, verified_by_us=False, tested_gpus=(), evidence=()),
)


def with_registry(key: str, name: str, data: str = "") -> Profile:
    value = RegistryValue(key, name, RegistryType.REG_SZ, data, "test")
    return replace(BASE, wine=WineSettings(registry=(value,)))


def rules(violations: list[Violation]) -> list[Rule]:
    return [violation.rule for violation in violations]


def test_every_rule_is_documented() -> None:
    assert set(RULE_DESCRIPTIONS) == set(Rule)


@pytest.mark.parametrize(
    "name",
    ["amtlib.dll", "AMTLIB", "adobe_licensing_wf.exe", "AGSService.exe", "adobegcclient.exe"],
)
def test_licensing_names_are_recognised(name: str) -> None:
    assert is_licensing_name(name)


@pytest.mark.parametrize("name", ["Photoshop.exe", "d3d11", "dxgi.dll", "msedgewebview2.exe"])
def test_ordinary_names_are_not_licensing(name: str) -> None:
    assert not is_licensing_name(name)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("Program Files/Common Files/Adobe/Adobe Desktop Common/NGL/x.exe", True),
        ("Program Files (x86)/Common Files/Adobe/OOBE/PDApp/core.dll", True),
        ("ProgramData\\Adobe\\SLStore\\data.bin", True),
        ("Program Files/Adobe/Adobe Photoshop 2026/amtlib.dll", True),
        ("Program Files/Adobe/Adobe Photoshop 2026/Photoshop.exe", False),
        ("", False),
    ],
)
def test_is_licensing_path(path: str, expected: bool) -> None:
    assert is_licensing_path(path) is expected


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("HKCU\\Software\\Wine", True),
        ("hkcu\\software\\wine\\direct3d", True),
        ("HKCU\\Software\\Wine\\AppDefaults\\Photoshop.exe", True),
        ("HKCU\\Control Panel\\Desktop", True),
        ("HKCU\\Software\\WineHQ", False),
        ("HKLM\\Software\\Adobe", False),
        ("HKCU\\Software\\Adobe\\CommonFiles", False),
    ],
)
def test_registry_allowlist(key: str, expected: bool) -> None:
    assert registry_key_allowed(key) is expected


def test_clean_profile_has_no_violations() -> None:
    settings = WineSettings(
        windows_version="win10",
        dll_overrides={"d3d11": "builtin"},
        env={"DXVK_LOG_LEVEL": "none"},
        registry=(
            RegistryValue(
                "HKCU\\Software\\Wine\\AppDefaults\\msedgewebview2.exe\\DllOverrides",
                "dxgi",
                RegistryType.REG_SZ,
                "builtin",
                "test",
            ),
        ),
    )
    assert check_profile(replace(BASE, wine=settings)) == []


def test_licensing_dll_override_is_rejected() -> None:
    profile = replace(BASE, wine=WineSettings(dll_overrides={"amtlib": "native"}))
    assert rules(check_profile(profile)) == [Rule.LICENSING_DLL_OVERRIDE]


def test_registry_key_outside_allowlist_is_rejected() -> None:
    profile = with_registry("HKLM\\Software\\Adobe\\Licensing", "Mode", "offline")
    assert rules(check_profile(profile)) == [Rule.REGISTRY_OUTSIDE_ALLOWLIST]


@pytest.mark.parametrize(
    "key",
    [
        "HKCU\\Software\\Wine\\DllOverrides",
        "HKCU\\Software\\Wine\\AppDefaults\\Photoshop.exe\\DllOverrides",
    ],
)
def test_licensing_override_through_registry_is_rejected(key: str) -> None:
    profile = with_registry(key, "adobe_licensing_wf", "disabled")
    assert rules(check_profile(profile)) == [Rule.LICENSING_DLL_OVERRIDE]


def test_raw_winedlloverrides_in_registry_is_rejected() -> None:
    profile = with_registry("HKCU\\Environment", "WINEDLLOVERRIDES", "d3d11=b")
    assert rules(check_profile(profile)) == [Rule.RAW_WINEDLLOVERRIDES]


def test_raw_winedlloverrides_in_env_is_rejected() -> None:
    profile = replace(BASE, wine=WineSettings(env={"WINEDLLOVERRIDES": "d3d11=b"}))
    assert rules(check_profile(profile)) == [Rule.RAW_WINEDLLOVERRIDES]


def test_launching_a_licensing_component_is_rejected() -> None:
    profile = replace(BASE, exe="Program Files/Common Files/Adobe/OOBE/PDApp/Core/PDapp.exe")
    assert rules(check_profile(profile)) == [Rule.LICENSING_EXECUTABLE]


def test_violations_accumulate() -> None:
    settings = WineSettings(dll_overrides={"amtlib": "native"}, env={"WINEDLLOVERRIDES": ""})
    found = rules(check_profile(replace(BASE, wine=settings)))
    assert found == [Rule.LICENSING_DLL_OVERRIDE, Rule.RAW_WINEDLLOVERRIDES]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "drive_c/Program Files/Adobe/Adobe Photoshop 2026/amtlib.dll",
            [Rule.VENDOR_DIRECTORY_WRITE],
        ),
        ("C:\\Program Files\\Common Files\\Adobe\\x.dll", [Rule.VENDOR_DIRECTORY_WRITE]),
        ("/p/drive_c/ProgramData/Adobe/SLStore/x", [Rule.VENDOR_DIRECTORY_WRITE]),
        ("/home/u/Downloads/NGL/patch.exe", [Rule.VENDOR_DIRECTORY_WRITE]),
        ("drive_c/users/u/../../Program Files/Adobe/x", [Rule.VENDOR_DIRECTORY_WRITE]),
        ("/p/drive_c/windows/system32/drivers/etc/hosts", [Rule.HOSTS_FILE_WRITE]),
        ("/etc/hosts", [Rule.HOSTS_FILE_WRITE]),
        ("etc/hosts", []),
        ("drive_c/users/u/Documents/poster.psd", []),
        ("drive_c/windows/Fonts/segoeui.ttf", []),
        ("/p/user.reg", []),
    ],
)
def test_check_write_target(path: str, expected: list[Rule]) -> None:
    assert rules(check_write_target(path)) == expected


def test_assert_write_allowed_raises_with_violations() -> None:
    with pytest.raises(ComplianceError) as excinfo:
        assert_write_allowed("drive_c/Program Files/Adobe/x.dll")
    assert rules(excinfo.value.violations) == [Rule.VENDOR_DIRECTORY_WRITE]
    assert "TUV005" in str(excinfo.value)


def test_assert_write_allowed_passes_clean_paths() -> None:
    assert_write_allowed("drive_c/users/u/Documents/poster.psd")


@pytest.mark.parametrize(
    ("key", "name", "expected"),
    [
        ("HKCU\\Software\\Wine\\WineDbg", "ShowCrashDialog", []),
        ("HKCU\\Software\\Wine\\AppDefaults\\App.exe\\DllOverrides", "dxgi", []),
        ("HKLM\\Software\\Adobe", "x", [Rule.REGISTRY_OUTSIDE_ALLOWLIST]),
        (
            "HKCU\\Software\\Wine\\AppDefaults\\App.exe\\DllOverrides",
            "amtlib",
            [Rule.LICENSING_DLL_OVERRIDE],
        ),
        ("HKCU\\Environment", "WineDllOverrides", [Rule.RAW_WINEDLLOVERRIDES]),
    ],
)
def test_check_registry_value(key: str, name: str, expected: list[Rule]) -> None:
    value = RegistryValue(key, name, RegistryType.REG_SZ, "", "test")
    violations = check_registry_value(value, "subject")
    assert rules(violations) == expected
    assert all(violation.subject == "subject" for violation in violations)


def test_violation_str_names_rule_and_subject() -> None:
    violation = Violation(Rule.HOSTS_FILE_WRITE, "hosts file", "/etc/hosts")
    assert str(violation) == "TUV006 /etc/hosts: hosts file"
