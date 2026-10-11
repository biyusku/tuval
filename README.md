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

## Try it

> `setup` has not yet been run end to end on a real machine, so expect rough edges.
> Please [report what happens](#report-your-results). Right now that is the most useful
> help there is.

### What you need

- A Linux desktop session: X11, or Wayland with XWayland. tuval does not run on Windows
  or macOS.
- `git` and Python ≥ 3.11.
- [uv](https://docs.astral.sh/uv/), the Python project manager tuval uses. If you don't
  have it yet:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- A Wine runtime: [AdobeWine](https://github.com/le-birnes/AdobeWine) (recommended), or
  Wine-Staging ≥ 11.10 from your distribution or [WineHQ](https://www.winehq.org/). See
  [Runtimes](#runtimes) for where tuval looks for it.
- `winetricks`. Most distributions package it under that name.
- Your own Adobe account with a valid licence.

### Get tuval

```bash
git clone https://github.com/biyusku/tuval.git && cd tuval
```

```bash
uv sync
```

`uv sync` creates a private environment for tuval in the `.venv` folder. It installs
nothing system-wide. Run every command below from the `tuval` folder, with `uv run` in
front.

### Set up Creative Cloud

1. **Check the machine.** `doctor` looks at the platform, display session, GPU and
   driver, Vulkan, winetricks, the Wine runtime and memory, and tells you what is
   missing.

   ```bash
   uv run tuval doctor
   ```

2. **See the plan.** This lists every command setup would run, and every registry value
   and environment variable it would set. It changes nothing.

   ```bash
   uv run tuval setup --dry-run
   ```

3. **Prepare the prefix.** A *prefix* is the folder where Wine keeps a Windows
   environment: its own `C:` drive and registry. This step creates
   `~/.local/share/tuval/prefixes/creative-cloud`, installs the Windows components the
   profiles need, applies their settings, and then checks the result for licence
   tampering.

   ```bash
   uv run tuval setup
   ```

4. **Download the installer** yourself, from Adobe:
   <https://creativecloud.adobe.com/apps/download/creative-cloud>. tuval never downloads
   vendor software.

5. **Run the installer inside the prefix.** Use the path of the file you downloaded; its
   name may differ from the example. Then follow Adobe's installer as you would on
   Windows.

   ```bash
   uv run tuval setup --installer ~/Downloads/Creative_Cloud_Set-Up.exe
   ```

Setup can be run again; it updates the existing prefix. It refuses to start, and
changes nothing, in any of these cases:

- it finds licence tampering in the prefix, or a hosts file that blocks Adobe;
- a profile breaks a compliance rule;
- the installer is not an `.exe`, or is itself a licensing component.

### What setup changes on your machine

- It never needs `sudo` or root, and it never changes system files. It reads
  `/etc/hosts` but does not write to it.
- It creates the prefix under `~/.local/share/tuval/prefixes/`, or under
  `$XDG_DATA_HOME/tuval/prefixes/` if that variable is set.
- winetricks keeps the components it downloads in its own cache (`~/.cache/winetricks`).
- Wine itself may add menu entries, icons and file associations under `~/.local/share`
  for the programs an installer sets up.

Deleting the prefix folder removes the Windows environment, including everything
installed into it.

### Report your results

Open an issue at <https://github.com/biyusku/tuval/issues>, whether it worked or not.
Please include:

- your distribution, GPU and graphics driver;
- the output of `uv run tuval doctor` and `uv run tuval setup --dry-run`;
- if a step failed, the last lines it printed.

The output contains paths inside your home folder, so it shows your user name. Replace
the name if you prefer.

## Commands

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

From the `tuval` folder, put `uv run` in front of each command, or activate the
environment once with `source .venv/bin/activate`. `doctor`, `profiles list`, `audit`
and `setup --dry-run` accept `--json`. Exit codes: `0` ok, `1` problems found, `2` usage
error.

## Runtimes

tuval looks for a runtime in this order. The first usable one wins:

| Order | Kind      | Where                                                                       |
|------:|-----------|-----------------------------------------------------------------------------|
| 1     | custom    | `$TUVAL_WINE`                                                               |
| 2     | adobewine | `/opt/adobewine/runtime/bin/wine`                                           |
| 3     | adobewine | `$ADOBEWINE_HOME/runtime/bin/wine` (default `~/.local/share/adobewine/...`) |
| 4     | system    | `wine` on `PATH`                                                            |

Recommended: AdobeWine, or Wine-Staging ≥ 11.10. To use a Wine that is somewhere else,
point `TUVAL_WINE` at its `wine` binary.

## App status (reported by others, not yet verified by tuval)

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
