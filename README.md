# tuval

**Run your own, legitimately licensed Adobe apps on Linux — safely, honestly, reproducibly.**

tuval ("canvas" in Turkish) is an open-source orchestration and safety layer around
Wine. It does not ship a Wine fork, and it never touches licensing. It picks a
known-good runtime, prepares one shared prefix, applies reviewed per-app settings, and
reports honestly how well each app works.

> **Status: pre-alpha.** Today tuval can check a machine (`doctor`), find Wine runtimes,
> show and validate app profiles, audit an existing prefix for licence tampering, and
> prepare the shared Creative Cloud prefix and run Adobe's own installer in it (`setup`).
> `setup` is tested on simulated hosts but has not yet been run end to end on a real
> machine. Launching apps is the next milestone. See
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#roadmap).

## Principles

1. **Your licence stays intact.** tuval never disables, renames, patches or blocks a
   licensing, activation or genuine-software component, and never writes into
   vendor-owned folders. This is enforced in code, not just promised. See
   [docs/COMPLIANCE.md](docs/COMPLIANCE.md).
2. **Honest status.** Every app profile has a tier (`platinum` … `broken`, `untested`)
   and evidence URLs. `platinum` and `gold` can only be claimed after the project has
   verified the app itself. The parser rejects any other claim.
3. **Build on the community, don't fork it.** Most of the Wine work for Adobe apps is
   done upstream and in [AdobeWine](https://github.com/le-birnes/AdobeWine). tuval uses
   those runtimes and adds what they lack: safety checks, diagnostics, packaging and
   distribution support. See [docs/LANDSCAPE.md](docs/LANDSCAPE.md).
4. **Nothing hidden.** Every registry value a profile sets carries a `why`. Everything
   tuval does to a prefix is declared in a TOML file you can read.

## What works today

```bash
tuval doctor                 # is this machine ready? (GPU, Vulkan, display, runtime, RAM)
tuval runtime list           # Wine runtimes found, best one marked with *
tuval profiles list          # bundled app profiles and their honest status
tuval profiles show photoshop
tuval profiles validate      # schema + compliance check (also for your own .toml files)
tuval audit [PREFIX]         # read-only scan of a prefix for licence tampering
tuval setup --dry-run        # show every step setup would take; changes nothing
tuval setup [--installer FILE]   # prepare the Creative Cloud prefix, then run Adobe's installer
```

`doctor`, `profiles list`, `audit` and `setup --dry-run` accept `--json`. Exit codes: `0`
ok, `1` problems found, `2` usage error.

### Setting up Creative Cloud

1. Check the machine: `tuval doctor`. Setup needs a runtime (below) and `winetricks`.
2. See what setup would do: `tuval setup --dry-run`. Every command, every registry value
   and every environment variable it sets is listed.
3. Prepare the prefix: `tuval setup`. This creates `~/.local/share/tuval/prefixes/creative-cloud`,
   installs the Windows components the profiles need, and applies their settings.
4. Download the official installer yourself from
   <https://creativecloud.adobe.com/apps/download/creative-cloud>. tuval never downloads
   vendor software.
5. Run it inside the prefix: `tuval setup --installer ~/Downloads/Creative_Cloud_Set-Up.exe`.

Setup can be run again; it updates the existing prefix. It refuses to start, and
changes nothing, if it finds licence tampering in the prefix or a hosts file that blocks
Adobe, if a profile breaks a compliance rule, or if the installer is not an `.exe` or is
itself a licensing component.

### Runtimes

tuval looks for a runtime in this order. The first usable one wins:

| Order | Kind      | Where                                                                       |
|------:|-----------|-----------------------------------------------------------------------------|
| 1     | custom    | `$TUVAL_WINE`                                                               |
| 2     | adobewine | `/opt/adobewine/runtime/bin/wine`                                           |
| 3     | adobewine | `$ADOBEWINE_HOME/runtime/bin/wine` (default `~/.local/share/adobewine/...`) |
| 4     | system    | `wine` on `PATH`                                                            |

Recommended: AdobeWine, or Wine-Staging ≥ 11.10.

### App status (reported by others, not yet verified by tuval)

| App                  | Tier     |
|----------------------|----------|
| Creative Cloud       | silver   |
| Photoshop            | silver   |
| Illustrator          | silver   |
| Media Encoder        | silver   |
| Audition             | silver   |
| Premiere Pro         | bronze   |
| After Effects        | bronze   |
| InDesign             | untested |
| Lightroom Classic    | broken   |

Almost all public reports come from NVIDIA GPUs with the proprietary driver. Reports
from AMD and Intel users are especially welcome.

## Install (development)

tuval needs Python ≥ 3.11 and has no runtime dependencies. With
[uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/biyusku/tuval.git && cd tuval
```

```bash
uv sync
```

```bash
uv run tuval doctor
```

## Development

```bash
uv run ruff format && uv run ruff check && uv run mypy && uv run pytest
```

The tool targets Linux, but the test suite runs on any OS. Every check reads the
system through an injectable `SystemProbe`, so tests build fake hosts under a temp
directory.

## Licence

GPL-3.0-or-later. tuval is not affiliated with or endorsed by Adobe. You need your own
valid licences for any software you run with it.
