# Architecture

tuval is a thin, testable layer **around** Wine. It does not replace or fork it.

```
            ┌─────────────────────────────── tuval ────────────────────────────────┐
 user ──▶   │ CLI ─▶ doctor · runtime · profiles · audit · setup · (run)           │
            │          │                                                           │
            │          ▼                                                           │
            │   SystemProbe · compliance guard (TUV001–006) · audit (TUV101–103)   │
            └──────────┬───────────────────────────────────────────────────────────┘
                       ▼
        pinned runtime: AdobeWine │ Wine-Staging ≥ 11.10 │ $TUVAL_WINE
                       ▼
        one shared prefix: ~/.local/share/tuval/prefixes/creative-cloud
```

## Layers

### Runtime (`runtime.py`)

tuval consumes runtimes and never builds one. Candidates, in priority order:
`$TUVAL_WINE`, AdobeWine (`/opt/adobewine`, then `$ADOBEWINE_HOME` or
`$XDG_DATA_HOME/adobewine`), then `wine` on `PATH`. Each candidate's `wine --version` is
parsed. Candidates that are missing, unparsable or duplicated are reported or dropped,
and the first usable one is selected. "Recommended" means ≥ 11.10, and for a system Wine
also Staging.

### Prefix model

Creative Cloud installs and licenses every app **inside its own prefix**, so all Adobe
apps share one prefix, `creative-cloud`. That gives two scopes of settings:

- **Base (prefix-wide):** the `creative-cloud` profile, e.g. graphics driver, crash
  dialog, winetricks verbs, and the WebView2 overrides the CC UI needs.
- **Per-app:** every other profile. Its Windows version and DLL overrides are applied
  under `HKCU\Software\Wine\AppDefaults\<exe>`, so one app's needs (Photoshop must
  report Windows 10) do not leak into the others.

Prefixes live at `$XDG_DATA_HOME/tuval/prefixes/<name>` (`paths.py`).

### Profiles (`profiles.py`, `data/profiles/*.toml`)

A declarative description of how to run one app and how well it works. Parsing is
strict: unknown keys, wrong types, bad URLs and dishonest claims are errors.

| Key                     | Meaning                                                                 |
|-------------------------|-------------------------------------------------------------------------|
| `schema`                | must be `1`                                                             |
| `id`                    | lowercase id; must match the file name                                  |
| `name`, `vendor`        | display strings                                                         |
| `prefix`                | prefix the app lives in (`creative-cloud`)                              |
| `installed_via`         | `creative-cloud` or `standalone-installer`                              |
| `exe`                   | POSIX path relative to `drive_c`; may contain a glob such as `20[0-9][0-9]` |
| `min_wine`              | minimum Wine version, e.g. `"11.10"`                                    |
| `requires`              | other profile ids that must be set up first                            |
| `mime_types`            | for desktop integration                                                 |
| `[wine]`                | `windows_version`, `winetricks`, `dll_overrides`, `env`, `[[wine.registry]]` (each with `why`) |
| `[status]`              | `tier`, `verified_by_us`, `tested_gpus`, `evidence` (https), `notes`    |
| `[[known_issues]]`      | `summary` and `source` (https)                                          |

Honesty rules: `platinum`/`gold` require `verified_by_us = true`; `untested` cannot be
verified; every tier except `untested` needs at least one evidence URL.

### Compliance guard (`compliance.py`) and audit (`audit.py`)

The guard checks every profile and every planned setup step: registry writes, the prefix
location, the installer and the environment. The audit inspects existing prefixes
read-only. Both are described in [COMPLIANCE.md](COMPLIANCE.md).

### Setup (`setup.py`)

`tuval setup` first builds a `Plan` and only then runs it, so `tuval setup --dry-run`
shows exactly the commands that would run. Planning:

1. Refuses on anything but Linux, without a usable runtime, or without the base
   `creative-cloud` profile. Warns when the runtime is older than the profiles'
   `min_wine`.
2. Takes the base profile plus every app profile that shares its prefix, and checks each
   one with the guard.
3. **Pre-flight:** audits the existing prefix (or, before the first run, the host's
   `/etc/hosts`) and refuses to continue if it finds licence tampering.
4. Finds the tools: winetricks (only when a profile needs verbs) and the `wineserver`
   that belongs to the selected runtime. A `wineserver` from `PATH` is only accepted for
   a system Wine, because a server from another Wine build will not talk to this one.
5. Builds the steps:

   | Step              | Command                                                                  |
   |-------------------|--------------------------------------------------------------------------|
   | `create-prefix`   | `wine wineboot --init`                                                   |
   | `winetricks`      | `winetricks --unattended <verbs>` (merged from all profiles, no duplicates) |
   | `windows-version` | `wine winecfg /v <version>` from the base profile                        |
   | `registry`        | `wine reg add …` for base values, base DLL overrides (`HKCU\Software\Wine\DllOverrides`) and per-app values under `AppDefaults\<exe>` |
   | `wait`            | `wineserver -w`, so the registry is on disk before it is checked         |
   | `audit`           | built in and read-only: the **self-check**                               |
   | `installer`       | `wine <installer.exe>`, only with `--installer`, without a timeout       |

6. Builds the environment: the user's environment without `WINEPREFIX` and
   `WINEDLLOVERRIDES` (dropped with a warning), then quiet defaults the user can override
   (`WINEDEBUG=fixme-all`, `WINETRICKS_LATEST_VERSION_CHECK=disabled`), then the base
   profile's `env`, then the forced `WINEPREFIX`, `WINE` and `WINESERVER`. Only these
   overrides are ever printed. The inherited environment may hold secrets, so it stays
   out of the output, the JSON and the plan's `repr`.
7. Runs the whole plan through the guard (`check_plan`, TUV001–TUV006) before returning
   it.

`execute` checks the plan once more, runs the steps in order and stops at the first
non-zero exit code. The self-check runs after `wineserver -w` and before the installer,
so tampering that appears during setup stops it. An app profile whose executable name is
a glob pattern cannot be scoped with `AppDefaults`; setup refuses rather than apply its
settings prefix-wide.

tuval never downloads vendor software. Without `--installer`, setup stops after the
self-check and points to the official download page.

### Doctor (`doctor.py`)

Checks, in order: platform, display session (Wine needs X11/XWayland), GPU vendor and
NVIDIA driver, Vulkan ICDs, winetricks, runtime, memory. Each returns
`ok`/`info`/`warn`/`fail`, and only `fail` makes the exit code non-zero.

### Testability

All host access goes through `SystemProbe(root, env, which, platform, version_reader)`.
Tests point `root` at a temp directory and fake the rest, so the Linux-only logic is
fully tested on any OS. Setup starts every command through an injected `Runner`, so
tests record the commands instead of starting Wine. The CLI writes results to an injected
stream and diagnostics to the `tuval` logger on stderr.

## Roadmap

| Milestone | Scope                                                                                     | State   |
|-----------|-------------------------------------------------------------------------------------------|---------|
| 0         | Profiles, compliance guard, audit, doctor, runtime discovery, CLI                        | done    |
| 1         | `tuval setup`: create the prefix, run winetricks, apply base + AppDefaults settings through the guard, run the vendor installer | done¹   |
| 2         | `tuval run <app>`, logs per app, `tuval report` to share a redacted diagnostic bundle    | next    |
| 3         | Desktop integration: `.desktop` files, icons, MIME associations                          | planned |
| 4         | Packaging: NixOS module (for anka-os), Flatpak/AUR/deb/rpm where feasible                | planned |
| 5         | VM fallback for apps rated `broken` (e.g. Lightroom Classic): a guided, licence-respecting Windows VM path | idea    |
| ongoing   | Verify profiles on real hardware (especially AMD/Intel), upstream fixes to Wine           | ongoing |

¹ Covered by tests on simulated hosts; not yet run end to end on a real Linux machine.
