# Compliance charter

tuval exists to run software **you have paid for** on an operating system its vendor
does not support. That only stays legitimate if tuval never helps anyone avoid paying.
These rules are therefore not guidelines. They are enforced by code
(`src/tuval/compliance.py`, `src/tuval/audit.py`) and by tests, and a pull request that
weakens them will not be merged.

## tuval will never

- disable, rename, delete, patch or replace a licensing, activation or genuine-software
  component (for Adobe, e.g. `amtlib`, `adobe_licensing_*`, `AGSService`, `AdobeGCClient`,
  anything under `NGL`, `OOBE`, `SLStore`, `SLCache`);
- add DLL overrides that change how such a component loads;
- block, redirect or proxy vendor activation or licensing servers (hosts file, DNS,
  firewall rules, proxies);
- write into vendor-owned install or licensing folders (`Program Files/Adobe`,
  `Program Files/Common Files/Adobe`, `ProgramData/Adobe`, and the `(x86)` variants);
- ship, download or point to cracked, patched or redistributed vendor binaries.

## tuval will

- install vendor software only through the vendor's own installer, inside a prefix the
  user owns;
- change only Wine and display settings (the registry allowlist below), each with a
  documented reason;
- report tampering it finds, which a "fix" from elsewhere may have caused, and tell the
  user how to restore it (the vendor's repair or reinstall). It never "repairs" anything
  itself;
- audit a prefix and the host's hosts file before setup touches them, and the prefix
  again after Wine has written it, and stop setup if anything is found.

When in doubt the guard errs towards refusing. A false positive costs a user one bug
report; a false negative could cost them their licence.

## Profile rules (enforced before anything is applied)

`tuval profiles validate` runs these on every bundled profile. `tuval setup` runs them
on the profiles it applies and again on its whole plan (every registry write, the prefix
location, the installer and the environment), and refuses to start on any violation.

| Rule   | Meaning                                                          |
|--------|------------------------------------------------------------------|
| TUV001 | DLL override targets a licensing/activation component            |
| TUV002 | Registry write outside the Wine/display allowlist                |
| TUV003 | Raw `WINEDLLOVERRIDES` bypasses per-DLL review                   |
| TUV004 | A profile or the setup installer launches a licensing/activation component |
| TUV005 | Write into a vendor-owned install or licensing directory         |
| TUV006 | Write to a hosts file (used to block activation servers)         |

Registry allowlist (prefixes):

- `HKCU\Software\Wine`
- `HKCU\Control Panel\Desktop`
- `HKCU\Environment` (but never `WINEDLLOVERRIDES`; see TUV003)
- `HKLM\System\CurrentControlSet\Hardware Profiles\Current\Software\Fonts`

## Audit rules (read-only, for existing prefixes)

`tuval audit [PREFIX]` inspects a prefix (by default tuval's own `creative-cloud` prefix)
and the host's `/etc/hosts`. It never modifies anything. `tuval setup` uses the same
checks as its pre-flight and self-check.

| Rule   | Finds                                                                                         |
|--------|-----------------------------------------------------------------------------------------------|
| TUV101 | Vendor hostnames redirected in the host's or the prefix's hosts file                          |
| TUV102 | A licensing component renamed out of the way (`.disabled`, `.bak`, `.orig`, `.old`)          |
| TUV103 | A DLL override for a licensing component (`DllOverrides`, per-app `AppDefaults`, or a `WINEDLLOVERRIDES` value stored in the registry) |

Example: some community helpers include a "sign-in fix" that renames
`NGL/adobe_licensing_wf.exe` to `adobe_licensing_wf.exe.disabled`. `tuval audit` reports
that as TUV102. tuval will not offer such a workaround. If sign-in is broken, the bug
belongs in Wine, and the fix should go upstream.

## Reporting

If you think tuval does something these rules forbid, please open an issue. If it is
sensitive, contact the maintainers privately first.
