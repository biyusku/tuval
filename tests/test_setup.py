from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.helpers import (
    ADOBEWINE_WINE,
    WINETRICKS,
    RecordingRunner,
    make_prefix,
    make_probe,
    refuse_commands,
    setup_host,
    write,
)
from tuval.audit import PREFIX_HOSTS_FILE
from tuval.compliance import ComplianceError, Rule, Violation
from tuval.doctor import SystemProbe
from tuval.paths import prefix_dir
from tuval.profiles import Profile, RegistryType, RegistryValue, WineSettings, load_bundled_profiles
from tuval.setup import (
    APP_DEFAULTS_KEY,
    WINE_KEY,
    Command,
    Plan,
    SetupError,
    Step,
    StepKind,
    check_plan,
    execute,
    plan_setup,
    plan_to_dict,
    run_command,
    step_label,
)

BLOCKING_HOSTS = "127.0.0.1 lcs-cops.adobe.io\n"
INSTALLER_NAME = "Creative_Cloud_Set-Up.exe"
# Stands in for anything private in the user's environment (cookies, keys, ...).
PRIVATE_NAME = "SESSION_COOKIE"
PRIVATE_VALUE = "private-value-1f3a"


def bundled() -> dict[str, Profile]:
    return load_bundled_profiles()


def app_profile(
    wine: WineSettings,
    *,
    exe: str = "Program Files/Example/App.exe",
    prefix: str = "creative-cloud",
) -> Profile:
    return replace(bundled()["photoshop"], id="app", exe=exe, prefix=prefix, wine=wine)


def with_app(app: Profile) -> dict[str, Profile]:
    return {**bundled(), app.id: app}


def default_prefix(probe: SystemProbe) -> Path:
    return prefix_dir("creative-cloud", probe.env)


def kinds(plan: Plan) -> list[StepKind]:
    return [step.kind for step in plan.steps]


def registry_steps(plan: Plan) -> list[RegistryValue]:
    return [step.registry for step in plan.steps if step.registry is not None]


def find_value(plan: Plan, key: str, name: str) -> RegistryValue:
    return next(v for v in registry_steps(plan) if v.key == key and v.name == name)


def rules(violations: list[Violation]) -> list[Rule]:
    return [violation.rule for violation in violations]


def no_progress(_line: str) -> None:
    return None


def installer_file(root: Path, name: str = INSTALLER_NAME) -> Path:
    return write(root / "Downloads" / name)


# --- planning: steps -------------------------------------------------------------------


def test_plan_runs_steps_in_order(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    steps = kinds(plan)

    assert steps[:3] == [StepKind.CREATE_PREFIX, StepKind.WINETRICKS, StepKind.WINDOWS_VERSION]
    assert steps[-2:] == [StepKind.WAIT, StepKind.AUDIT]
    assert set(steps[3:-2]) == {StepKind.REGISTRY}


def test_plan_uses_the_selected_runtime(tmp_path: Path) -> None:
    wine = str(tmp_path.joinpath(*ADOBEWINE_WINE))

    plan = plan_setup(setup_host(tmp_path), bundled())

    assert plan.steps[0].argv == (wine, "wineboot", "--init")
    assert plan.steps[-2].argv == (str(Path(wine).with_name("wineserver")), "-w")
    assert plan.env_overrides["WINE"] == wine


def test_plan_installs_base_winetricks_verbs(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    expected = (WINETRICKS, "--unattended", "d3dcompiler_47", "corefonts", "tahoma")
    assert plan.steps[1].argv == expected


def test_plan_sets_the_prefix_wide_windows_version(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    assert plan.steps[2].argv[1:] == ("winecfg", "/v", "win11")


def test_plan_scopes_app_windows_version_to_its_executable(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    value = find_value(plan, f"{APP_DEFAULTS_KEY}\\Photoshop.exe", "Version")

    assert value.data == "win10"


def test_plan_writes_base_registry_values(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    step = next(s for s in plan.steps if s.registry and s.registry.name == "ShowCrashDialog")

    assert step.argv[1:4] == ("reg", "add", f"{WINE_KEY}\\WineDbg")
    assert step.argv[-6:] == ("ShowCrashDialog", "/t", "REG_DWORD", "/d", "0", "/f")


def test_plan_only_writes_wine_registry_keys_for_bundled_profiles(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    assert all(value.key.startswith(WINE_KEY + "\\") for value in registry_steps(plan))
    assert check_plan(plan) == []


def test_plan_puts_base_dll_overrides_prefix_wide(tmp_path: Path) -> None:
    profiles = bundled()
    base = profiles["creative-cloud"]
    profiles["creative-cloud"] = replace(
        base, wine=replace(base.wine, dll_overrides={"d3d11": "builtin"})
    )

    plan = plan_setup(setup_host(tmp_path), profiles)

    assert find_value(plan, f"{WINE_KEY}\\DllOverrides", "d3d11").data == "builtin"


def test_plan_scopes_app_dll_overrides_to_its_executable(tmp_path: Path) -> None:
    app = app_profile(WineSettings(dll_overrides={"dxgi": "native"}))

    plan = plan_setup(setup_host(tmp_path), with_app(app))

    assert find_value(plan, f"{APP_DEFAULTS_KEY}\\App.exe\\DllOverrides", "dxgi").data == "native"


def test_plan_merges_winetricks_verbs_without_duplicates(tmp_path: Path) -> None:
    app = app_profile(WineSettings(winetricks=("corefonts", "vcrun2022")))

    plan = plan_setup(setup_host(tmp_path), with_app(app))

    assert plan.steps[1].argv[2:] == ("d3dcompiler_47", "corefonts", "tahoma", "vcrun2022")


def test_plan_ignores_profiles_in_other_prefixes(tmp_path: Path) -> None:
    app = app_profile(WineSettings(windows_version="win7"), prefix="elsewhere")

    plan = plan_setup(setup_host(tmp_path), with_app(app))

    assert all("App.exe" not in value.key for value in registry_steps(plan))


def test_plan_skips_winetricks_when_no_profile_needs_it(tmp_path: Path) -> None:
    profiles = bundled()
    base = profiles["creative-cloud"]
    profiles["creative-cloud"] = replace(base, wine=replace(base.wine, winetricks=()))

    plan = plan_setup(setup_host(tmp_path, programs={}), profiles)

    assert StepKind.WINETRICKS not in kinds(plan)


def test_plan_skips_winecfg_without_a_windows_version(tmp_path: Path) -> None:
    profiles = bundled()
    base = profiles["creative-cloud"]
    profiles["creative-cloud"] = replace(base, wine=replace(base.wine, windows_version=None))

    plan = plan_setup(setup_host(tmp_path), profiles)

    assert StepKind.WINDOWS_VERSION not in kinds(plan)
    assert kinds(plan)[-1] is StepKind.AUDIT


def test_plan_refuses_app_settings_for_a_pattern_executable(tmp_path: Path) -> None:
    app = app_profile(WineSettings(windows_version="win10"), exe="Program Files/X/App[0-9].exe")

    with pytest.raises(SetupError, match="is a pattern"):
        plan_setup(setup_host(tmp_path), with_app(app))


# --- planning: runtime and tools -------------------------------------------------------


def test_plan_requires_linux(tmp_path: Path) -> None:
    probe = replace(setup_host(tmp_path), platform="win32")

    with pytest.raises(SetupError, match="Linux only"):
        plan_setup(probe, bundled())


def test_plan_requires_a_runtime(tmp_path: Path) -> None:
    with pytest.raises(SetupError, match="no usable Wine runtime"):
        plan_setup(make_probe(tmp_path), bundled())


def test_plan_requires_the_base_profile(tmp_path: Path) -> None:
    profiles = bundled()
    del profiles["creative-cloud"]

    with pytest.raises(SetupError, match="'creative-cloud' profile is missing"):
        plan_setup(setup_host(tmp_path), profiles)


def test_plan_warns_about_an_old_runtime(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    probe = setup_host(tmp_path, version="wine-11.0 (Staging)\n")

    with caplog.at_level(logging.WARNING):
        plan_setup(probe, bundled())

    assert "older than Wine 11.10" in caplog.text


def test_plan_requires_winetricks(tmp_path: Path) -> None:
    with pytest.raises(SetupError, match="winetricks is required"):
        plan_setup(setup_host(tmp_path, programs={}), bundled())


def test_plan_requires_the_runtimes_own_wineserver(tmp_path: Path) -> None:
    probe = setup_host(tmp_path, programs={"winetricks": WINETRICKS, "wineserver": "/usr/bin/x"})
    tmp_path.joinpath(*ADOBEWINE_WINE).with_name("wineserver").unlink()

    with pytest.raises(SetupError, match="no wineserver next to"):
        plan_setup(probe, bundled())


def test_plan_finds_the_system_wineserver_on_path(tmp_path: Path) -> None:
    wine = write(tmp_path / "usr" / "bin" / "wine")
    programs = {"wine": str(wine), "wineserver": "/usr/bin/wineserver", "winetricks": WINETRICKS}
    probe = make_probe(tmp_path, programs=programs, versions={wine: "wine-11.18\n"})

    plan = plan_setup(probe, bundled())

    assert plan.env_overrides["WINESERVER"] == "/usr/bin/wineserver"


# --- planning: environment -------------------------------------------------------------


def test_plan_forces_the_prefix_location(tmp_path: Path) -> None:
    probe = setup_host(tmp_path, extra_env={"WINEPREFIX": "/somewhere/else"})

    plan = plan_setup(probe, bundled())

    assert plan.env["WINEPREFIX"] == str(default_prefix(probe))
    assert plan.prefix == default_prefix(probe)


def test_plan_drops_inherited_dll_overrides(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    probe = setup_host(tmp_path, extra_env={"WINEDLLOVERRIDES": "amtlib=d"})

    with caplog.at_level(logging.WARNING):
        plan = plan_setup(probe, bundled())

    assert "WINEDLLOVERRIDES" not in plan.env
    assert "ignoring WINEDLLOVERRIDES" in caplog.text


def test_plan_sets_quiet_defaults(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    assert plan.env_overrides["WINEDEBUG"] == "fixme-all"
    assert plan.env_overrides["WINETRICKS_LATEST_VERSION_CHECK"] == "disabled"


def test_plan_respects_the_users_wine_debug(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path, extra_env={"WINEDEBUG": "+loaddll"}), bundled())

    assert "WINEDEBUG" not in plan.env_overrides
    assert plan.env["WINEDEBUG"] == "+loaddll"


def test_plan_keeps_the_inherited_environment_out_of_its_repr(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path, extra_env={PRIVATE_NAME: PRIVATE_VALUE}), bundled())

    assert plan.env[PRIVATE_NAME] == PRIVATE_VALUE
    assert PRIVATE_VALUE not in repr(plan)
    assert PRIVATE_VALUE not in json.dumps(plan_to_dict(plan))


# --- planning: installer ---------------------------------------------------------------


def test_plan_without_installer_stops_after_the_self_check(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    assert not plan.runs_installer
    assert plan.steps[-1].kind is StepKind.AUDIT


def test_plan_runs_the_installer_last_without_a_timeout(tmp_path: Path) -> None:
    installer = installer_file(tmp_path)

    plan = plan_setup(setup_host(tmp_path), bundled(), installer=installer)

    last = plan.steps[-1]
    assert plan.runs_installer
    assert last.argv == (str(tmp_path.joinpath(*ADOBEWINE_WINE)), str(installer.resolve()))
    assert last.timeout is None


def test_plan_accepts_an_upper_case_exe_suffix(tmp_path: Path) -> None:
    installer = installer_file(tmp_path, "SETUP.EXE")

    plan = plan_setup(setup_host(tmp_path), bundled(), installer=installer)

    assert plan.runs_installer


def test_plan_requires_the_installer_to_exist(tmp_path: Path) -> None:
    with pytest.raises(SetupError, match="installer not found"):
        plan_setup(setup_host(tmp_path), bundled(), installer=tmp_path / INSTALLER_NAME)


def test_plan_requires_a_windows_executable_installer(tmp_path: Path) -> None:
    installer = installer_file(tmp_path, "setup.msi")

    with pytest.raises(SetupError, match=r"must be a Windows \.exe"):
        plan_setup(setup_host(tmp_path), bundled(), installer=installer)


def test_plan_refuses_to_launch_a_licensing_component(tmp_path: Path) -> None:
    installer = installer_file(tmp_path, "adobe_licensing_wf.exe")

    with pytest.raises(ComplianceError) as caught:
        plan_setup(setup_host(tmp_path), bundled(), installer=installer)

    assert rules(caught.value.violations) == [Rule.LICENSING_EXECUTABLE]


# --- planning: compliance --------------------------------------------------------------


def test_plan_refuses_a_non_compliant_profile(tmp_path: Path) -> None:
    app = app_profile(WineSettings(dll_overrides={"amtlib": "native"}))

    with pytest.raises(ComplianceError) as caught:
        plan_setup(setup_host(tmp_path), with_app(app))

    assert rules(caught.value.violations) == [Rule.LICENSING_DLL_OVERRIDE]


def test_plan_refuses_a_tampered_prefix(tmp_path: Path) -> None:
    probe = setup_host(tmp_path)
    prefix = make_prefix(default_prefix(probe))
    write(prefix.joinpath(*PREFIX_HOSTS_FILE), BLOCKING_HOSTS)

    with pytest.raises(SetupError, match="TUV101"):
        plan_setup(probe, bundled())


def test_plan_refuses_a_renamed_licensing_component(tmp_path: Path) -> None:
    probe = setup_host(tmp_path)
    prefix = make_prefix(default_prefix(probe))
    oobe = prefix / "drive_c" / "Program Files (x86)" / "Common Files" / "Adobe" / "OOBE"
    write(oobe / "PDApp.dll.bak")

    with pytest.raises(SetupError, match="TUV102"):
        plan_setup(probe, bundled())


@pytest.mark.parametrize("has_prefix", [False, True])
def test_plan_refuses_a_host_that_blocks_the_vendor(tmp_path: Path, has_prefix: bool) -> None:
    probe = setup_host(tmp_path)
    if has_prefix:
        make_prefix(default_prefix(probe))
    write(tmp_path / "etc" / "hosts", BLOCKING_HOSTS)

    with pytest.raises(SetupError, match="licence tampering found"):
        plan_setup(probe, bundled())


def test_check_plan_flags_registry_writes_outside_the_allowlist(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())
    value = RegistryValue("HKLM\\Software\\Adobe", "x", RegistryType.REG_SZ, "1", "test")
    step = Step(StepKind.REGISTRY, "bad", ("wine",), registry=value)

    violations = check_plan(replace(plan, steps=(*plan.steps, step)))

    assert rules(violations) == [Rule.REGISTRY_OUTSIDE_ALLOWLIST]


def test_check_plan_flags_licensing_dll_overrides(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())
    value = RegistryValue(f"{WINE_KEY}\\DllOverrides", "amtlib", RegistryType.REG_SZ, "", "x")
    step = Step(StepKind.REGISTRY, "bad", ("wine",), registry=value)

    violations = check_plan(replace(plan, steps=(*plan.steps, step)))

    assert rules(violations) == [Rule.LICENSING_DLL_OVERRIDE]


def test_check_plan_flags_raw_dll_overrides_in_the_environment(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())
    env = {**plan.env_overrides, "WINEDLLOVERRIDES": "amtlib=d"}

    assert rules(check_plan(replace(plan, env_overrides=env))) == [Rule.RAW_WINEDLLOVERRIDES]


def test_check_plan_flags_a_prefix_inside_a_vendor_directory(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())
    vendor = Path("/x/drive_c/Program Files/Adobe/prefix")

    assert rules(check_plan(replace(plan, prefix=vendor))) == [Rule.VENDOR_DIRECTORY_WRITE]


# --- execution -------------------------------------------------------------------------


def test_execute_runs_every_command_in_order(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())
    runner = RecordingRunner()
    lines: list[str] = []

    execute(plan, runner, lines.append)

    assert [c.argv for c in runner.commands] == [s.argv for s in plan.steps if s.argv]
    assert lines == [step_label(i, len(plan.steps), s) for i, s in enumerate(plan.steps, 1)]


def test_execute_passes_the_combined_environment(tmp_path: Path) -> None:
    probe = setup_host(tmp_path, extra_env={"WINEDLLOVERRIDES": "x=n", "LANG": "tr_TR.UTF-8"})
    plan = plan_setup(probe, bundled())
    runner = RecordingRunner()

    execute(plan, runner, no_progress)

    for command in runner.commands:
        assert command.env["WINEPREFIX"] == str(plan.prefix)
        assert command.env["LANG"] == "tr_TR.UTF-8"
        assert "WINEDLLOVERRIDES" not in command.env


def test_execute_runs_the_installer_in_the_new_prefix(tmp_path: Path) -> None:
    installer = installer_file(tmp_path)
    plan = plan_setup(setup_host(tmp_path), bundled(), installer=installer)
    runner = RecordingRunner()

    execute(plan, runner, no_progress)

    assert runner.commands[-1].argv[-1] == str(installer.resolve())
    assert runner.commands[-1].timeout is None


def test_execute_reuses_an_existing_prefix(tmp_path: Path) -> None:
    probe = setup_host(tmp_path)
    make_prefix(default_prefix(probe))
    plan = plan_setup(probe, bundled())
    runner = RecordingRunner()

    execute(plan, runner, no_progress)

    assert runner.commands[0].argv[1:] == ("wineboot", "--init")


def test_execute_stops_at_the_first_failing_step(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())
    runner = RecordingRunner(failing="winecfg")

    with pytest.raises(SetupError, match=r"step 3/\d+ failed with exit code 1"):
        execute(plan, runner, no_progress)

    assert "winecfg" in runner.commands[-1].argv


def test_execute_fails_when_wine_did_not_create_the_prefix(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    with pytest.raises(SetupError, match="Wine did not create the prefix"):
        execute(plan, RecordingRunner(creates_prefix=False), no_progress)


def test_execute_reports_a_prefix_directory_it_cannot_create(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())
    write(plan.prefix.parent)

    with pytest.raises(SetupError, match="cannot create"):
        execute(plan, refuse_commands, no_progress)


def test_execute_detects_tampering_during_setup(tmp_path: Path) -> None:
    probe = setup_host(tmp_path)
    plan = plan_setup(probe, bundled())
    recorder = RecordingRunner()

    def tampering_runner(command: Command) -> int:
        code = recorder(command)
        write(plan.prefix.joinpath(*PREFIX_HOSTS_FILE), BLOCKING_HOSTS)
        return code

    with pytest.raises(SetupError, match="TUV101"):
        execute(plan, tampering_runner, no_progress)


def test_execute_refuses_a_non_compliant_plan(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())
    tampered = replace(plan, env_overrides={**plan.env_overrides, "WINEDLLOVERRIDES": "x"})

    with pytest.raises(ComplianceError):
        execute(tampered, refuse_commands, no_progress)

    assert not plan.prefix.parent.exists()


# --- running commands ------------------------------------------------------------------


def python_command(code: str, timeout: float) -> Command:
    return Command((sys.executable, "-I", "-c", code), dict(os.environ), timeout)


def test_run_command_returns_the_exit_code() -> None:
    assert run_command(python_command("raise SystemExit(3)", timeout=60)) == 3


def test_run_command_reports_a_missing_program(tmp_path: Path) -> None:
    command = Command((str(tmp_path / "no-such-program"),), dict(os.environ), 60)

    with pytest.raises(SetupError, match="could not start"):
        run_command(command)


def test_run_command_stops_a_command_that_runs_too_long() -> None:
    command = python_command("import time; time.sleep(60)", timeout=0.5)

    with pytest.raises(SetupError, match=r"did not finish within 0\.5 s"):
        run_command(command)


# --- serialisation ---------------------------------------------------------------------


def test_plan_to_dict_describes_every_step(tmp_path: Path) -> None:
    plan = plan_setup(setup_host(tmp_path), bundled())

    data = json.loads(json.dumps(plan_to_dict(plan)))

    assert data["prefix"] == str(plan.prefix)
    assert data["runtime"]["kind"] == "adobewine"
    assert data["runtime"]["version"] == "wine-11.18 (Staging)"
    assert [step["kind"] for step in data["steps"]] == [step.kind.value for step in plan.steps]
    assert data["steps"][-1]["argv"] == []
