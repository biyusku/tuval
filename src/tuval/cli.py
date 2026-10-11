"""Command-line interface.

Results go to ``Context.out`` (stdout by default); diagnostics go to the logger (stderr).
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TextIO

from tuval import __version__
from tuval.audit import AuditError, audit_prefix
from tuval.compliance import ComplianceError, check_profile
from tuval.doctor import SystemProbe, has_failures, run_checks
from tuval.log import configure_logging, get_logger
from tuval.paths import prefix_dir
from tuval.profiles import (
    Profile,
    ProfileError,
    Status,
    Tier,
    index_profiles,
    load_bundled_profiles,
    load_profile_file,
)
from tuval.runtime import discover_runtimes, select_runtime
from tuval.setup import (
    INSTALLER_DOWNLOAD_URL,
    Plan,
    Runner,
    SetupError,
    execute,
    plan_setup,
    plan_to_dict,
    run_command,
    step_label,
)

logger = get_logger("cli")

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2
DEFAULT_PREFIX_NAME = "creative-cloud"


def _stdout() -> TextIO:
    return sys.stdout


@dataclass(frozen=True)
class Context:
    """Everything a command touches outside itself; tests inject a fake host via ``probe``
    and record commands instead of running them via ``runner``."""

    out: TextIO = field(default_factory=_stdout)
    probe: SystemProbe = field(default_factory=SystemProbe)
    runner: Runner = run_command


Handler = Callable[[argparse.Namespace, Context], int]


def _emit(ctx: Context, text: str = "") -> None:
    ctx.out.write(text + "\n")


def _emit_json(ctx: Context, data: object) -> None:
    _emit(ctx, json.dumps(data, indent=2, default=str))


def _cmd_doctor(args: argparse.Namespace, ctx: Context) -> int:
    results = run_checks(ctx.probe)
    if args.json:
        _emit_json(ctx, [asdict(result) for result in results])
    else:
        for result in results:
            _emit(ctx, f"[{result.status.value.upper():>4}] {result.name}: {result.detail}")
    return EXIT_PROBLEMS if has_failures(results) else EXIT_OK


def _profile_summary(profile: Profile) -> dict[str, object]:
    return {
        "id": profile.id,
        "name": profile.name,
        "tier": profile.status.tier.value,
        "verified_by_us": profile.status.verified_by_us,
        "tested_gpus": list(profile.status.tested_gpus),
    }


def _status_origin(status: Status) -> tuple[str, str]:
    """Short and long wording for who stands behind a tier."""
    if status.verified_by_us:
        return "verified", "verified by tuval"
    if status.tier is Tier.UNTESTED:
        return "-", "no reports yet"
    return "reported", "reported by others"


def _cmd_profiles_list(args: argparse.Namespace, ctx: Context) -> int:
    profiles = load_bundled_profiles()
    if args.json:
        _emit_json(ctx, [_profile_summary(profile) for profile in profiles.values()])
        return EXIT_OK
    for profile in profiles.values():
        origin, _ = _status_origin(profile.status)
        _emit(ctx, f"{profile.id:<18} {profile.status.tier.value:<9} {origin:<9} {profile.name}")
    return EXIT_OK


def _show_wine(ctx: Context, profile: Profile) -> None:
    wine = profile.wine
    if wine.windows_version:
        _emit(ctx, f"  windows version: {wine.windows_version}")
    if wine.winetricks:
        _emit(ctx, f"  winetricks:      {' '.join(wine.winetricks)}")
    for dll, mode in wine.dll_overrides.items():
        _emit(ctx, f"  dll override:    {dll}={mode}")
    for name, value in wine.env.items():
        _emit(ctx, f"  env:             {name}={value}")
    for reg in wine.registry:
        _emit(ctx, f"  registry:        {reg.key}\\{reg.name} = {reg.data} ({reg.kind.value})")
        _emit(ctx, f"                   why: {reg.why}")


def _cmd_profiles_show(args: argparse.Namespace, ctx: Context) -> int:
    profiles = load_bundled_profiles()
    profile = profiles.get(args.id)
    if profile is None:
        logger.error("unknown profile %r; try 'tuval profiles list'", args.id)
        return EXIT_USAGE
    status = profile.status
    _emit(ctx, f"{profile.name} ({profile.id}) — {profile.vendor}")
    _, origin = _status_origin(status)
    _emit(ctx, f"  status:          {status.tier.value} ({origin})")
    _emit(ctx, f"  tested GPUs:     {', '.join(status.tested_gpus) or 'none'}")
    _emit(ctx, f"  prefix:          {profile.prefix} (installed via {profile.installed_via.value})")
    _emit(ctx, f"  executable:      drive_c/{profile.exe}")
    _emit(ctx, f"  minimum Wine:    {profile.min_wine}")
    if profile.requires:
        _emit(ctx, f"  requires:        {', '.join(profile.requires)}")
    _show_wine(ctx, profile)
    if status.notes:
        _emit(ctx, f"  notes:           {status.notes}")
    for url in status.evidence:
        _emit(ctx, f"  evidence:        {url}")
    for issue in profile.known_issues:
        _emit(ctx, f"  known issue:     {issue.summary} <{issue.source}>")
    return EXIT_OK


def _validate(files: Sequence[str]) -> list[str]:
    """Return human-readable problems; empty means everything is valid and compliant."""
    try:
        bundled = load_bundled_profiles()
        extra = [load_profile_file(Path(name)) for name in files]
        combined = {**bundled, **{profile.id: profile for profile in extra}}
        index_profiles(combined.values())
    except ProfileError as exc:
        return [str(exc)]
    targets = extra if files else list(bundled.values())
    return [str(violation) for profile in targets for violation in check_profile(profile)]


def _cmd_profiles_validate(args: argparse.Namespace, ctx: Context) -> int:
    problems = _validate(args.files)
    for problem in problems:
        _emit(ctx, f"error: {problem}")
    if problems:
        return EXIT_PROBLEMS
    _emit(ctx, "all profiles valid and compliant")
    return EXIT_OK


def _cmd_runtime_list(_args: argparse.Namespace, ctx: Context) -> int:
    probe = ctx.probe
    runtimes = discover_runtimes(
        probe.env, root=probe.root, which=probe.which, reader=probe.version_reader
    )
    selected = select_runtime(runtimes)
    if not runtimes:
        _emit(ctx, "no Wine runtime found; see 'tuval doctor'")
        return EXIT_PROBLEMS
    for runtime in runtimes:
        marker = "*" if runtime is selected else " "
        version = runtime.info.raw if runtime.info else f"unusable: {runtime.problem}"
        _emit(ctx, f"{marker} {runtime.kind.value:<10} {version:<28} {runtime.wine}")
    return EXIT_OK if selected else EXIT_PROBLEMS


def _cmd_audit(args: argparse.Namespace, ctx: Context) -> int:
    probe = ctx.probe
    prefix = Path(args.prefix) if args.prefix else prefix_dir(DEFAULT_PREFIX_NAME, probe.env)
    try:
        findings = audit_prefix(prefix, host_root=probe.root)
    except AuditError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE
    if args.json:
        _emit_json(ctx, [asdict(finding) for finding in findings])
    elif findings:
        for finding in findings:
            _emit(ctx, str(finding))
    else:
        _emit(ctx, f"{prefix}: no licensing tampering found")
    return EXIT_PROBLEMS if findings else EXIT_OK


def _emit_progress(ctx: Context, line: str) -> None:
    # Flush so the step line appears before the output of the command it announces.
    _emit(ctx, line)
    ctx.out.flush()


def _show_plan_header(ctx: Context, plan: Plan) -> None:
    runtime = plan.runtime
    version = runtime.info.raw if runtime.info else "unknown version"
    _emit(ctx, f"prefix:  {plan.prefix}")
    _emit(ctx, f"runtime: {runtime.kind.value} {version} ({runtime.wine})")


def _show_plan(ctx: Context, plan: Plan) -> None:
    _show_plan_header(ctx, plan)
    for name, value in plan.env_overrides.items():
        _emit(ctx, f"env:     {name}={value}")
    total = len(plan.steps)
    for index, step in enumerate(plan.steps, start=1):
        _emit(ctx, step_label(index, total, step))
        command = shlex.join(step.argv) if step.argv else "(built into tuval; read-only)"
        _emit(ctx, f"    {command}")
    _emit(ctx, "dry run: nothing was changed")


def _show_next_step(ctx: Context, plan: Plan) -> None:
    if plan.runs_installer:
        _emit(ctx, "setup finished")
        return
    _emit(ctx, "the prefix is ready; now download the official Creative Cloud installer from")
    _emit(ctx, f"  {INSTALLER_DOWNLOAD_URL}")
    _emit(ctx, "and run: tuval setup --installer FILE")


def _setup(args: argparse.Namespace, ctx: Context) -> int:
    installer = Path(args.installer) if args.installer else None
    plan = plan_setup(ctx.probe, load_bundled_profiles(), installer=installer)
    if args.json:
        _emit_json(ctx, plan_to_dict(plan))
        return EXIT_OK
    if args.dry_run:
        _show_plan(ctx, plan)
        return EXIT_OK
    _show_plan_header(ctx, plan)
    execute(plan, ctx.runner, lambda line: _emit_progress(ctx, line))
    _show_next_step(ctx, plan)
    return EXIT_OK


def _cmd_setup(args: argparse.Namespace, ctx: Context) -> int:
    if args.json and not args.dry_run:
        logger.error("--json only works together with --dry-run")
        return EXIT_USAGE
    try:
        return _setup(args, ctx)
    except SetupError as exc:
        logger.error("%s", exc)
    except ComplianceError as exc:
        for violation in exc.violations:
            _emit(ctx, f"error: {violation}")
    return EXIT_PROBLEMS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tuval",
        description="Run your own, legitimately licensed Windows creative apps on Linux.",
    )
    parser.add_argument("--version", action="version", version=f"tuval {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="more diagnostics")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    doctor = commands.add_parser("doctor", help="check whether this machine is ready")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_cmd_doctor)

    profiles = commands.add_parser("profiles", help="inspect application profiles")
    profile_commands = profiles.add_subparsers(dest="profiles_command", required=True)
    listing = profile_commands.add_parser("list", help="list bundled profiles")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=_cmd_profiles_list)
    show = profile_commands.add_parser("show", help="show one profile")
    show.add_argument("id")
    show.set_defaults(handler=_cmd_profiles_show)
    validate = profile_commands.add_parser("validate", help="validate profiles and compliance")
    validate.add_argument("files", nargs="*", metavar="FILE")
    validate.set_defaults(handler=_cmd_profiles_validate)

    runtime = commands.add_parser("runtime", help="Wine runtimes")
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_commands.add_parser("list", help="list detected runtimes").set_defaults(
        handler=_cmd_runtime_list
    )

    audit = commands.add_parser("audit", help="read-only check of a prefix for licence tampering")
    audit.add_argument("prefix", nargs="?", help=f"default: the '{DEFAULT_PREFIX_NAME}' prefix")
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(handler=_cmd_audit)

    setup = commands.add_parser(
        "setup", help="prepare the Creative Cloud prefix and run the vendor's installer"
    )
    setup.add_argument(
        "--installer", metavar="FILE", help="the official installer .exe you downloaded"
    )
    setup.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    setup.add_argument("--json", action="store_true", help="print the plan as JSON (dry run)")
    setup.set_defaults(handler=_cmd_setup)
    return parser


def run(argv: Sequence[str] | None, ctx: Context) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    handler: Handler = args.handler
    try:
        return handler(args, ctx)
    except ProfileError as exc:
        logger.error("%s", exc)
        return EXIT_PROBLEMS


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv, Context())
