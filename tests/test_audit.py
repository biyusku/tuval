from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import EMPTY_REGISTRY, make_prefix, write
from tuval.audit import (
    MAX_SHOWN_HOSTNAMES,
    PREFIX_HOSTS_FILE,
    AuditError,
    AuditRule,
    Finding,
    audit_hosts_file,
    audit_prefix,
    audit_registry_file,
    audit_renamed_components,
)


def rules(findings: list[Finding]) -> list[AuditRule]:
    return [finding.rule for finding in findings]


def test_clean_prefix_has_no_findings(tmp_path: Path) -> None:
    prefix = make_prefix(tmp_path / "prefix")
    write(prefix.joinpath(*PREFIX_HOSTS_FILE), "127.0.0.1 localhost\n")
    write(tmp_path / "host" / "etc" / "hosts", "127.0.0.1 localhost\n::1 localhost\n")
    assert audit_prefix(prefix, host_root=tmp_path / "host") == []


def test_non_prefix_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(AuditError, match="does not look like a Wine prefix"):
        audit_prefix(tmp_path)


def test_prefix_without_drive_c_is_still_audited(tmp_path: Path) -> None:
    write(tmp_path / "system.reg", EMPTY_REGISTRY)
    assert audit_prefix(tmp_path) == []


# --- hosts files (TUV101) ---------------------------------------------------------------


def test_hosts_entries_for_vendor_are_reported(tmp_path: Path) -> None:
    hosts = write(
        tmp_path / "hosts",
        "127.0.0.1 localhost\n0.0.0.0 lmlicensing.adobe.com  # added by a 'fix'\n",
    )
    [finding] = audit_hosts_file(hosts)
    assert finding.rule is AuditRule.HOSTS_BLOCKS_VENDOR
    assert "1 vendor hostname(s)" in finding.message
    assert "lmlicensing.adobe.com" in finding.message


def test_hosts_message_is_truncated(tmp_path: Path) -> None:
    lines = [f"0.0.0.0 host{n}.adobe.io" for n in range(MAX_SHOWN_HOSTNAMES + 2)]
    [finding] = audit_hosts_file(write(tmp_path / "hosts", "\n".join(lines)))
    assert f"{MAX_SHOWN_HOSTNAMES + 2} vendor hostname(s)" in finding.message
    assert f"host{MAX_SHOWN_HOSTNAMES}.adobe.io" not in finding.message
    assert ", ..." in finding.message


def test_commented_hosts_entries_are_ignored(tmp_path: Path) -> None:
    hosts = write(tmp_path / "hosts", "# 0.0.0.0 lmlicensing.adobe.com\n127.0.0.1 localhost\n")
    assert audit_hosts_file(hosts) == []


def test_missing_hosts_file_is_not_a_finding(tmp_path: Path) -> None:
    assert audit_hosts_file(tmp_path / "hosts") == []


def test_host_hosts_file_is_checked_when_requested(tmp_path: Path) -> None:
    prefix = make_prefix(tmp_path / "prefix")
    write(tmp_path / "host" / "etc" / "hosts", "0.0.0.0 genuine.adobe.com\n")
    assert audit_prefix(prefix) == []
    findings = audit_prefix(prefix, host_root=tmp_path / "host")
    assert rules(findings) == [AuditRule.HOSTS_BLOCKS_VENDOR]


def test_prefix_hosts_file_is_checked(tmp_path: Path) -> None:
    prefix = make_prefix(tmp_path / "prefix")
    write(prefix.joinpath(*PREFIX_HOSTS_FILE), "127.0.0.1 ic.adobe.io\n")
    assert rules(audit_prefix(prefix)) == [AuditRule.HOSTS_BLOCKS_VENDOR]


# --- renamed components (TUV102) --------------------------------------------------------


def test_renamed_amtlib_in_app_folder_is_reported(tmp_path: Path) -> None:
    app = tmp_path / "Program Files" / "Adobe" / "Adobe Photoshop 2026"
    write(app / "amtlib.dll.bak")
    write(app / "Photoshop.exe")
    [finding] = audit_renamed_components(tmp_path)
    assert finding.rule is AuditRule.LICENSING_COMPONENT_RENAMED
    assert "Adobe Photoshop 2026/amtlib.dll" in finding.message


def test_disabled_ngl_component_is_reported(tmp_path: Path) -> None:
    # What AdobeWine's fix-signin does: rename the NGL licensing helper out of the way.
    ngl = tmp_path / "Program Files" / "Common Files" / "Adobe" / "Adobe Desktop Common" / "NGL"
    write(ngl / "adobe_licensing_wf.exe.disabled")
    [finding] = audit_renamed_components(tmp_path)
    assert "NGL/adobe_licensing_wf.exe" in finding.message


def test_vendor_directories_are_matched_case_insensitively(tmp_path: Path) -> None:
    write(tmp_path / "PROGRAMDATA" / "ADOBE" / "SLStore" / "cache.db.old")
    assert rules(audit_renamed_components(tmp_path)) == [AuditRule.LICENSING_COMPONENT_RENAMED]


def test_renamed_ordinary_files_are_ignored(tmp_path: Path) -> None:
    write(tmp_path / "Program Files" / "Adobe" / "Adobe Photoshop 2026" / "Photoshop.exe.bak")
    write(tmp_path / "Program Files" / "Common Files" / "Adobe" / "Fonts" / "font.otf.old")
    assert audit_renamed_components(tmp_path) == []


def test_intact_licensing_files_are_not_findings(tmp_path: Path) -> None:
    write(tmp_path / "Program Files" / "Common Files" / "Adobe" / "NGL" / "adobe_licensing_wf.exe")
    assert audit_renamed_components(tmp_path) == []


def test_licensing_names_outside_vendor_directories_are_ignored(tmp_path: Path) -> None:
    write(tmp_path / "users" / "u" / "Downloads" / "amtlib.dll.bak")
    assert audit_renamed_components(tmp_path) == []


# --- registry overrides (TUV103) --------------------------------------------------------


def test_registry_dll_override_for_licensing_is_reported(tmp_path: Path) -> None:
    reg = write(
        tmp_path / "user.reg",
        EMPTY_REGISTRY
        + "\n[Software\\\\Wine\\\\DllOverrides] 1700000000\n"
        + '"d3d11"="builtin"\n'
        + '"*amtlib"="native"\n',
    )
    [finding] = audit_registry_file(reg)
    assert finding.rule is AuditRule.LICENSING_DLL_OVERRIDE
    assert "'*amtlib'" in finding.message
    assert "[Software\\Wine\\DllOverrides]" in finding.message


def test_per_app_dll_override_for_licensing_is_reported(tmp_path: Path) -> None:
    reg = write(
        tmp_path / "user.reg",
        "[Software\\\\Wine\\\\AppDefaults\\\\Photoshop.exe\\\\DllOverrides]\n"
        + '"adobe_licensing_wf"="disabled"\n',
    )
    assert rules(audit_registry_file(reg)) == [AuditRule.LICENSING_DLL_OVERRIDE]


def test_winedlloverrides_environment_value_is_parsed(tmp_path: Path) -> None:
    reg = write(
        tmp_path / "user.reg",
        '[Environment]\n"WINEDLLOVERRIDES"="d3d11,dxgi=b;amtlib=n"\n',
    )
    [finding] = audit_registry_file(reg)
    assert "'amtlib'" in finding.message


def test_harmless_overrides_are_not_findings(tmp_path: Path) -> None:
    reg = write(
        tmp_path / "user.reg",
        "[Software\\\\Wine\\\\DllOverrides]\n"
        + '"d3d11"="builtin"\n'
        + '"dxgi"="native,builtin"\n'
        + "[Software\\\\Adobe]\n"
        + '"amtlib"="not an override"\n'
        + '[Environment]\n"WINEDLLOVERRIDES"="d3d11=b"\n',
    )
    assert audit_registry_file(reg) == []


def test_prefix_registry_files_are_audited(tmp_path: Path) -> None:
    prefix = make_prefix(tmp_path / "prefix")
    write(prefix / "system.reg", '[Software\\\\Wine\\\\DllOverrides]\n"amtlib"="native"\n')
    findings = audit_prefix(prefix)
    assert rules(findings) == [AuditRule.LICENSING_DLL_OVERRIDE]
    assert findings[0].path == str(prefix / "system.reg")


def test_finding_str_names_rule_and_path() -> None:
    finding = Finding(AuditRule.HOSTS_BLOCKS_VENDOR, "blocked", "/etc/hosts")
    assert str(finding) == "TUV101 /etc/hosts: blocked"
