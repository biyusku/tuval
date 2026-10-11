from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tests.helpers import (
    RecordingRunner,
    healthy_host,
    make_prefix,
    make_probe,
    refuse_commands,
    setup_host,
    write,
)
from tuval import __version__
from tuval.cli import EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, Context, main, run
from tuval.doctor import SystemProbe
from tuval.paths import prefix_dir
from tuval.setup import INSTALLER_DOWNLOAD_URL, Runner

BUNDLED_PROFILE_COUNT = 9

PROFILE_WITH_LICENSING_OVERRIDE = """
schema = 1
id = "sneaky"
name = "Sneaky"
vendor = "Example"
prefix = "creative-cloud"
installed_via = "creative-cloud"
exe = "Program Files/Example/Sneaky.exe"
min_wine = "11.10"

[wine.dll_overrides]
amtlib = "native"

[status]
tier = "untested"
verified_by_us = false
"""


def invoke(
    argv: list[str], probe: SystemProbe, runner: Runner = refuse_commands
) -> tuple[int, str]:
    out = io.StringIO()
    code = run(argv, Context(out=out, probe=probe, runner=runner))
    return code, out.getvalue()


def default_prefix(probe: SystemProbe) -> Path:
    return prefix_dir("creative-cloud", probe.env)


def test_profiles_list(tmp_path: Path) -> None:
    code, out = invoke(["profiles", "list"], make_probe(tmp_path))
    lines = out.splitlines()
    assert code == EXIT_OK
    assert len(lines) == BUNDLED_PROFILE_COUNT
    assert any(line.startswith("photoshop ") and "reported" in line for line in lines)


def test_profiles_list_does_not_claim_reports_for_untested(tmp_path: Path) -> None:
    code, out = invoke(["profiles", "list"], make_probe(tmp_path))
    indesign = next(line for line in out.splitlines() if line.startswith("indesign "))
    assert code == EXIT_OK
    assert indesign.split() == ["indesign", "untested", "-", "InDesign"]


def test_profiles_list_json(tmp_path: Path) -> None:
    code, out = invoke(["profiles", "list", "--json"], make_probe(tmp_path))
    data = json.loads(out)
    assert code == EXIT_OK
    assert len(data) == BUNDLED_PROFILE_COUNT
    assert set(data[0]) == {"id", "name", "tier", "verified_by_us", "tested_gpus"}


def test_profiles_show(tmp_path: Path) -> None:
    code, out = invoke(["profiles", "show", "photoshop"], make_probe(tmp_path))
    assert code == EXIT_OK
    assert out.startswith("Photoshop (photoshop)")
    assert "  status:          silver (reported by others)" in out
    assert "  requires:        creative-cloud" in out
    assert "  evidence:        https://" in out


def test_profiles_show_untested(tmp_path: Path) -> None:
    code, out = invoke(["profiles", "show", "indesign"], make_probe(tmp_path))
    assert code == EXIT_OK
    assert "  status:          untested (no reports yet)" in out


def test_profiles_show_lists_wine_settings(tmp_path: Path) -> None:
    code, out = invoke(["profiles", "show", "creative-cloud"], make_probe(tmp_path))
    assert code == EXIT_OK
    assert "  registry:        HKCU\\Software\\Wine" in out
    assert "why: " in out


def test_profiles_show_unknown_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = invoke(["profiles", "show", "nope"], make_probe(tmp_path))
    assert code == EXIT_USAGE
    assert out == ""
    assert "unknown profile 'nope'" in capsys.readouterr().err


def test_profiles_validate_bundled(tmp_path: Path) -> None:
    code, out = invoke(["profiles", "validate"], make_probe(tmp_path))
    assert (code, out) == (EXIT_OK, "all profiles valid and compliant\n")


def test_profiles_validate_rejects_licensing_override(tmp_path: Path) -> None:
    path = write(tmp_path / "sneaky.toml", PROFILE_WITH_LICENSING_OVERRIDE)
    code, out = invoke(["profiles", "validate", str(path)], make_probe(tmp_path))
    assert code == EXIT_PROBLEMS
    assert out == "error: TUV001 sneaky: amtlib: licensing DLL\n"


def test_profiles_validate_reports_parse_errors(tmp_path: Path) -> None:
    path = write(tmp_path / "broken.toml", "schema = = 1\n")
    code, out = invoke(["profiles", "validate", str(path)], make_probe(tmp_path))
    assert code == EXIT_PROBLEMS
    assert out.startswith("error: ")
    assert "invalid TOML" in out


def test_doctor_on_healthy_host(tmp_path: Path) -> None:
    code, out = invoke(["doctor"], healthy_host(tmp_path))
    assert code == EXIT_OK
    assert out.splitlines()[0] == "[  OK] platform: Linux"


def test_doctor_json(tmp_path: Path) -> None:
    code, out = invoke(["doctor", "--json"], healthy_host(tmp_path))
    assert code == EXIT_OK
    assert {entry["status"] for entry in json.loads(out)} == {"ok"}


def test_doctor_reports_failures(tmp_path: Path) -> None:
    code, out = invoke(["doctor"], make_probe(tmp_path))
    assert code == EXIT_PROBLEMS
    assert "[FAIL] runtime: none found" in out


def test_runtime_list_marks_selected_runtime(tmp_path: Path) -> None:
    code, out = invoke(["runtime", "list"], healthy_host(tmp_path))
    assert code == EXIT_OK
    assert out.startswith("* adobewine  wine-11.18 (Staging)")


def test_runtime_list_without_runtimes(tmp_path: Path) -> None:
    code, out = invoke(["runtime", "list"], make_probe(tmp_path))
    assert (code, out) == (EXIT_PROBLEMS, "no Wine runtime found; see 'tuval doctor'\n")


def test_runtime_list_shows_unusable_runtimes(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "TUVAL_WINE": str(tmp_path / "missing")}
    code, out = invoke(["runtime", "list"], make_probe(tmp_path, env=env))
    assert code == EXIT_PROBLEMS
    assert out.startswith("  custom     unusable: TUVAL_WINE points to a missing file")


def test_audit_default_prefix_clean(tmp_path: Path) -> None:
    probe = make_probe(tmp_path)
    prefix = make_prefix(default_prefix(probe))
    code, out = invoke(["audit"], probe)
    assert (code, out) == (EXIT_OK, f"{prefix}: no licensing tampering found\n")


def test_audit_reports_host_hosts_file(tmp_path: Path) -> None:
    probe = make_probe(tmp_path)
    make_prefix(default_prefix(probe))
    write(tmp_path / "etc" / "hosts", "0.0.0.0 lmlicensing.adobe.com\n")
    code, out = invoke(["audit"], probe)
    assert code == EXIT_PROBLEMS
    assert out.startswith("TUV101 ")


def test_audit_json(tmp_path: Path) -> None:
    prefix = make_prefix(tmp_path / "custom-prefix")
    write(prefix / "user.reg", '[Software\\\\Wine\\\\DllOverrides]\n"amtlib"="native"\n')
    code, out = invoke(["audit", str(prefix), "--json"], make_probe(tmp_path))
    [finding] = json.loads(out)
    assert code == EXIT_PROBLEMS
    assert finding["rule"] == "TUV103"
    assert finding["path"] == str(prefix / "user.reg")


def test_audit_rejects_non_prefix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = invoke(["audit", str(tmp_path / "missing")], make_probe(tmp_path))
    assert (code, out) == (EXIT_USAGE, "")
    assert "does not look like a Wine prefix" in capsys.readouterr().err


def test_setup_dry_run_changes_nothing(tmp_path: Path) -> None:
    probe = setup_host(tmp_path)
    code, out = invoke(["setup", "--dry-run"], probe)
    lines = out.splitlines()
    assert code == EXIT_OK
    assert lines[0] == f"prefix:  {default_prefix(probe)}"
    assert lines[1].startswith("runtime: adobewine wine-11.18 (Staging)")
    assert f"env:     WINEPREFIX={default_prefix(probe)}" in lines
    assert any(line.startswith("[1/") and "Create or update" in line for line in lines)
    assert "    (built into tuval; read-only)" in lines
    assert lines[-1] == "dry run: nothing was changed"
    assert not default_prefix(probe).exists()


def test_setup_dry_run_json(tmp_path: Path) -> None:
    probe = setup_host(tmp_path, extra_env={"SESSION_COOKIE": "private-value-1f3a"})
    code, out = invoke(["setup", "--dry-run", "--json"], probe)
    data = json.loads(out)
    assert code == EXIT_OK
    assert data["steps"][0]["kind"] == "create-prefix"
    assert data["env"]["WINEPREFIX"] == str(default_prefix(probe))
    assert "private-value-1f3a" not in out


def test_setup_json_requires_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = invoke(["setup", "--json"], setup_host(tmp_path))
    assert (code, out) == (EXIT_USAGE, "")
    assert "--json only works together with --dry-run" in capsys.readouterr().err


def test_setup_prepares_the_prefix_and_points_to_the_installer(tmp_path: Path) -> None:
    probe = setup_host(tmp_path)
    runner = RecordingRunner()
    code, out = invoke(["setup"], probe, runner)
    assert code == EXIT_OK
    assert runner.commands
    assert (default_prefix(probe) / "drive_c").is_dir()
    assert f"  {INSTALLER_DOWNLOAD_URL}" in out.splitlines()
    assert out.endswith("and run: tuval setup --installer FILE\n")


def test_setup_runs_the_supplied_installer(tmp_path: Path) -> None:
    installer = write(tmp_path / "Downloads" / "Creative_Cloud_Set-Up.exe")
    runner = RecordingRunner()
    code, out = invoke(["setup", "--installer", str(installer)], setup_host(tmp_path), runner)
    assert code == EXIT_OK
    assert runner.commands[-1].argv[-1] == str(installer.resolve())
    assert out.endswith("setup finished\n")


def test_setup_without_runtime(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = invoke(["setup"], make_probe(tmp_path))
    assert (code, out) == (EXIT_PROBLEMS, "")
    assert "no usable Wine runtime found" in capsys.readouterr().err


def test_setup_refuses_a_licensing_installer(tmp_path: Path) -> None:
    installer = write(tmp_path / "adobe_licensing_wf.exe")
    code, out = invoke(["setup", "--installer", str(installer)], setup_host(tmp_path))
    assert code == EXIT_PROBLEMS
    assert out.startswith("error: TUV004 ")


def test_setup_reports_a_failed_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = RecordingRunner(failing="wineboot")
    code, out = invoke(["setup"], setup_host(tmp_path), runner)
    assert code == EXIT_PROBLEMS
    assert len(runner.commands) == 1
    assert "[1/" in out
    assert "step 1/" in capsys.readouterr().err


def test_version_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        invoke(["--version"], make_probe(tmp_path))
    assert excinfo.value.code == EXIT_OK
    assert capsys.readouterr().out == f"tuval {__version__}\n"


def test_command_is_required(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        invoke([], make_probe(tmp_path))
    assert excinfo.value.code == EXIT_USAGE


def test_main_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-v", "profiles", "validate"]) == EXIT_OK
    assert capsys.readouterr().out == "all profiles valid and compliant\n"
