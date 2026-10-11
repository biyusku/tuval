"""``tuval setup``: prepare the shared Wine prefix and run the vendor's own installer.

Setup is planned before anything runs. The whole plan — every command and every registry
value — passes the compliance guard first, so ``tuval setup --dry-run`` shows exactly what
would happen. tuval never downloads vendor software: the user supplies the official
installer, and tuval only starts it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

from tuval.audit import AuditError, Finding, audit_hosts_file, audit_prefix
from tuval.compliance import (
    UNIX_HOSTS_FILE,
    ComplianceError,
    Rule,
    Violation,
    check_profile,
    check_registry_value,
    check_write_target,
    is_licensing_name,
)
from tuval.doctor import SystemProbe
from tuval.log import get_logger
from tuval.paths import prefix_dir
from tuval.profiles import Profile, RegistryType, RegistryValue
from tuval.runtime import Runtime, RuntimeKind, discover_runtimes, select_runtime

logger = get_logger("setup")

BASE_PROFILE_ID = "creative-cloud"
INSTALLER_DOWNLOAD_URL = "https://creativecloud.adobe.com/apps/download/creative-cloud"
INSTALLER_SUFFIX = ".exe"

WINEBOOT_TIMEOUT_S = 600.0
WINETRICKS_TIMEOUT_S = 3600.0
WINE_TOOL_TIMEOUT_S = 120.0
WAIT_TIMEOUT_S = 120.0

WINE_KEY = "HKCU\\Software\\Wine"
APP_DEFAULTS_KEY = WINE_KEY + "\\AppDefaults"
DLL_OVERRIDES_SUBKEY = "DllOverrides"
VERSION_VALUE = "Version"

# Defaults that a value already set in the user's environment wins over.
DEFAULT_ENV: Mapping[str, str] = {
    "WINEDEBUG": "fixme-all",
    "WINETRICKS_LATEST_VERSION_CHECK": "disabled",
}
_WINEDLLOVERRIDES = "WINEDLLOVERRIDES"
# Never inherited: tuval owns the prefix location, and raw overrides would bypass the guard.
_DROPPED_ENV = frozenset({"WINEPREFIX", _WINEDLLOVERRIDES})
_PATTERN_CHARS = frozenset("[]*?")
_LINUX = "linux"


class SetupError(RuntimeError):
    """Setup cannot continue; the message says why and what to do next."""


class StepKind(StrEnum):
    CREATE_PREFIX = "create-prefix"
    WINETRICKS = "winetricks"
    WINDOWS_VERSION = "windows-version"
    REGISTRY = "registry"
    WAIT = "wait"
    AUDIT = "audit"
    INSTALLER = "installer"


@dataclass(frozen=True)
class Step:
    """One unit of setup; ``argv`` is empty for steps tuval performs itself."""

    kind: StepKind
    description: str
    argv: tuple[str, ...] = ()
    timeout: float | None = None
    registry: RegistryValue | None = None


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    env: Mapping[str, str] = field(repr=False)
    timeout: float | None


Runner = Callable[[Command], int]
Progress = Callable[[str], None]


@dataclass(frozen=True)
class Plan:
    """What setup will do. Only ``env_overrides`` is ever shown: the inherited
    environment may hold secrets, so it stays out of output and ``repr``."""

    prefix: Path
    host_root: Path
    runtime: Runtime
    env_overrides: Mapping[str, str]
    inherited_env: Mapping[str, str] = field(repr=False)
    steps: tuple[Step, ...]

    @property
    def env(self) -> dict[str, str]:
        return {**self.inherited_env, **self.env_overrides}

    @property
    def runs_installer(self) -> bool:
        return any(step.kind is StepKind.INSTALLER for step in self.steps)


@dataclass(frozen=True)
class _Scope:
    """The base profile and the applications that share its prefix."""

    base: Profile
    apps: tuple[Profile, ...]

    @property
    def profiles(self) -> tuple[Profile, ...]:
        return (self.base, *self.apps)

    @property
    def winetricks_verbs(self) -> tuple[str, ...]:
        verbs = (verb for profile in self.profiles for verb in profile.wine.winetricks)
        return tuple(dict.fromkeys(verbs))


@dataclass(frozen=True)
class _Tools:
    wine: str
    wineserver: str
    winetricks: str | None


def step_label(index: int, total: int, step: Step) -> str:
    return f"[{index}/{total}] {step.description}"


def _scope(profiles: Mapping[str, Profile]) -> _Scope:
    base = profiles.get(BASE_PROFILE_ID)
    if base is None:
        raise SetupError(f"the {BASE_PROFILE_ID!r} profile is missing")
    apps = sorted(
        (p for p in profiles.values() if p.prefix == base.prefix and p.id != base.id),
        key=lambda profile: profile.id,
    )
    violations = [violation for p in (base, *apps) for violation in check_profile(p)]
    if violations:
        raise ComplianceError(violations)
    return _Scope(base, tuple(apps))


def _require_runtime(probe: SystemProbe, scope: _Scope) -> Runtime:
    runtimes = discover_runtimes(
        probe.env, root=probe.root, which=probe.which, reader=probe.version_reader
    )
    runtime = select_runtime(runtimes)
    if runtime is None or runtime.info is None:
        raise SetupError("no usable Wine runtime found; see 'tuval doctor'")
    needed = max(profile.min_wine for profile in scope.profiles)
    if runtime.info.version < needed:
        logger.warning("%s is older than Wine %s; expect problems", runtime.info.raw, needed)
    return runtime


def _find_wineserver(probe: SystemProbe, runtime: Runtime) -> str:
    sibling = runtime.wine.with_name("wineserver")
    if sibling.is_file():
        return str(sibling)
    # A wineserver from a different Wine build refuses to talk to this wine, so only the
    # system runtime may fall back to PATH.
    found = probe.which("wineserver") if runtime.kind is RuntimeKind.SYSTEM else None
    if found is None:
        raise SetupError(f"no wineserver next to {runtime.wine}; is the runtime complete?")
    return found


def _find_tools(probe: SystemProbe, runtime: Runtime, scope: _Scope) -> _Tools:
    winetricks = None
    if scope.winetricks_verbs:
        winetricks = probe.which("winetricks")
        if winetricks is None:
            raise SetupError("winetricks is required; install it from your distribution")
    return _Tools(str(runtime.wine), _find_wineserver(probe, runtime), winetricks)


def _refuse_findings(findings: Sequence[Finding]) -> None:
    if findings:
        details = "\n".join(f"  {finding}" for finding in findings)
        raise SetupError(
            f"licence tampering found; restore a clean state first (see 'tuval audit'):\n{details}"
        )


def _preflight(prefix: Path, host_root: Path) -> None:
    """Refuse to touch a prefix, or a host, that already blocks or bypasses licensing."""
    try:
        findings = audit_prefix(prefix, host_root=host_root)
    except AuditError:
        # No prefix yet, but a hosts file blocking the vendor still matters.
        findings = audit_hosts_file(host_root.joinpath(*UNIX_HOSTS_FILE))
    _refuse_findings(findings)


def _shown(data: str | int) -> str:
    return f'"{data}"' if isinstance(data, str) else str(data)


def _registry_step(wine: str, value: RegistryValue) -> Step:
    argv = (wine, "reg", "add", value.key, "/v", value.name, "/t", value.kind.value)
    return Step(
        StepKind.REGISTRY,
        f"Set {value.key}\\{value.name} = {_shown(value.data)}",
        (*argv, "/d", str(value.data), "/f"),
        WINE_TOOL_TIMEOUT_S,
        value,
    )


def _dll_override_values(key: str, profile: Profile) -> list[RegistryValue]:
    return [
        RegistryValue(key, dll, RegistryType.REG_SZ, mode, f"{profile.id}: DLL override")
        for dll, mode in profile.wine.dll_overrides.items()
    ]


def _app_defaults_key(profile: Profile) -> str:
    name = PurePosixPath(profile.exe).name
    if _PATTERN_CHARS & set(name):
        raise SetupError(
            f"profile {profile.id!r}: executable name {name!r} is a pattern, so its "
            "per-application settings cannot be scoped"
        )
    return f"{APP_DEFAULTS_KEY}\\{name}"


def _app_values(profile: Profile) -> list[RegistryValue]:
    """Settings Wine's AppDefaults scope to one executable instead of the whole prefix."""
    wine = profile.wine
    values: list[RegistryValue] = []
    if wine.windows_version or wine.dll_overrides:
        key = _app_defaults_key(profile)
        if wine.windows_version:
            why = f"{profile.id}: Windows version"
            values.append(
                RegistryValue(key, VERSION_VALUE, RegistryType.REG_SZ, wine.windows_version, why)
            )
        values.extend(_dll_override_values(f"{key}\\{DLL_OVERRIDES_SUBKEY}", profile))
    values.extend(wine.registry)
    return values


def _registry_values(scope: _Scope) -> list[RegistryValue]:
    base = scope.base
    values = [
        *base.wine.registry,
        *_dll_override_values(f"{WINE_KEY}\\{DLL_OVERRIDES_SUBKEY}", base),
    ]
    for app in scope.apps:
        values.extend(_app_values(app))
    return values


def _prefix_steps(tools: _Tools, scope: _Scope) -> list[Step]:
    """Steps that build the prefix, ending with a read-only self-check."""
    steps = [
        Step(
            StepKind.CREATE_PREFIX,
            "Create or update the Wine prefix",
            (tools.wine, "wineboot", "--init"),
            WINEBOOT_TIMEOUT_S,
        )
    ]
    verbs = scope.winetricks_verbs
    if verbs and tools.winetricks is not None:
        argv = (tools.winetricks, "--unattended", *verbs)
        description = f"Install Windows components: {' '.join(verbs)}"
        steps.append(Step(StepKind.WINETRICKS, description, argv, WINETRICKS_TIMEOUT_S))
    version = scope.base.wine.windows_version
    if version:
        argv = (tools.wine, "winecfg", "/v", version)
        description = f"Report Windows version {version}"
        steps.append(Step(StepKind.WINDOWS_VERSION, description, argv, WINE_TOOL_TIMEOUT_S))
    steps.extend(_registry_step(tools.wine, value) for value in _registry_values(scope))
    description = "Wait for Wine to finish writing the prefix"
    steps.append(Step(StepKind.WAIT, description, (tools.wineserver, "-w"), WAIT_TIMEOUT_S))
    steps.append(Step(StepKind.AUDIT, "Check the prefix for licence tampering"))
    return steps


def _installer_step(wine: str, installer: Path) -> Step:
    if not installer.is_file():
        raise SetupError(f"installer not found: {installer}")
    if installer.suffix.lower() != INSTALLER_SUFFIX:
        raise SetupError(f"the installer must be a Windows {INSTALLER_SUFFIX} file: {installer}")
    path = installer.resolve()
    return Step(StepKind.INSTALLER, f"Run the installer {path.name}", (wine, str(path)))


def _inherited_env(env: Mapping[str, str]) -> dict[str, str]:
    if _WINEDLLOVERRIDES in env:
        logger.warning(
            "ignoring %s from your environment; tuval only applies reviewed DLL overrides",
            _WINEDLLOVERRIDES,
        )
    return {name: value for name, value in env.items() if name not in _DROPPED_ENV}


def _env_overrides(
    inherited: Mapping[str, str], base: Profile, forced: Mapping[str, str]
) -> dict[str, str]:
    overrides = {name: value for name, value in DEFAULT_ENV.items() if name not in inherited}
    overrides.update(base.wine.env)
    overrides.update(forced)
    return overrides


def check_plan(plan: Plan) -> list[Violation]:
    """Compliance violations in a plan; setup refuses to run unless this is empty."""
    violations = check_write_target(plan.prefix)
    for step in plan.steps:
        if step.registry is not None:
            violations.extend(check_registry_value(step.registry, step.description))
        if step.kind is StepKind.INSTALLER and step.argv and is_licensing_name(step.argv[-1]):
            violations.append(
                Violation(Rule.LICENSING_EXECUTABLE, "licensing component", step.argv[-1])
            )
    if _WINEDLLOVERRIDES in plan.env:
        violations.append(Violation(Rule.RAW_WINEDLLOVERRIDES, "use dll_overrides", "environment"))
    return violations


def assert_plan_allowed(plan: Plan) -> None:
    violations = check_plan(plan)
    if violations:
        raise ComplianceError(violations)


def plan_setup(
    probe: SystemProbe, profiles: Mapping[str, Profile], *, installer: Path | None = None
) -> Plan:
    """Work out every step. Raises before anything changes if setup must not proceed."""
    if not probe.platform.startswith(_LINUX):
        raise SetupError("tuval setup runs on Linux only")
    scope = _scope(profiles)
    runtime = _require_runtime(probe, scope)
    prefix = prefix_dir(scope.base.prefix, probe.env)
    _preflight(prefix, probe.root)
    tools = _find_tools(probe, runtime, scope)
    steps = _prefix_steps(tools, scope)
    if installer is not None:
        steps.append(_installer_step(tools.wine, installer))
    inherited = _inherited_env(probe.env)
    forced = {"WINEPREFIX": str(prefix), "WINE": tools.wine, "WINESERVER": tools.wineserver}
    plan = Plan(
        prefix=prefix,
        host_root=probe.root,
        runtime=runtime,
        env_overrides=_env_overrides(inherited, scope.base, forced),
        inherited_env=inherited,
        steps=tuple(steps),
    )
    assert_plan_allowed(plan)
    return plan


def plan_to_dict(plan: Plan) -> dict[str, object]:
    """A JSON-ready view of the plan; the inherited environment is deliberately left out."""
    info = plan.runtime.info
    return {
        "prefix": str(plan.prefix),
        "runtime": {
            "kind": plan.runtime.kind.value,
            "wine": str(plan.runtime.wine),
            "version": info.raw if info else None,
        },
        "env": dict(plan.env_overrides),
        "steps": [
            {"kind": step.kind.value, "description": step.description, "argv": list(step.argv)}
            for step in plan.steps
        ],
    }


def run_command(command: Command) -> int:
    """Run one command with its output going straight to the user's terminal."""
    try:
        completed = subprocess.run(
            list(command.argv), env=dict(command.env), timeout=command.timeout, check=False
        )
    except OSError as exc:
        raise SetupError(f"could not start {command.argv[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SetupError(f"{command.argv[0]} did not finish within {exc.timeout:g} s") from exc
    return completed.returncode


def _self_check(plan: Plan) -> None:
    try:
        findings = audit_prefix(plan.prefix, host_root=plan.host_root)
    except AuditError as exc:
        raise SetupError(f"Wine did not create the prefix: {exc}") from exc
    _refuse_findings(findings)


def execute(plan: Plan, runner: Runner, progress: Progress) -> None:
    """Run the plan in order; the first failing step stops setup with :class:`SetupError`."""
    assert_plan_allowed(plan)
    try:
        plan.prefix.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SetupError(f"cannot create {plan.prefix.parent}: {exc}") from exc
    env = plan.env
    total = len(plan.steps)
    for index, step in enumerate(plan.steps, start=1):
        progress(step_label(index, total, step))
        if step.kind is StepKind.AUDIT:
            _self_check(plan)
            continue
        code = runner(Command(step.argv, env, step.timeout))
        if code != 0:
            raise SetupError(
                f"step {index}/{total} failed with exit code {code}: {step.description}"
            )
