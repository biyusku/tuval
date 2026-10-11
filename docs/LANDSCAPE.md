# Landscape (as of October 2026)

Why tuval exists, and what it builds on. Statements here summarise the linked sources.
Check them for current details, as this area moves quickly.

## Upstream Wine

- Patches that make the Adobe installers and Photoshop work landed first in
  Wine-Staging 11.1/11.2, contributed by PhialsBasement, and have been moving into
  mainline Wine since.
  - [Phoronix: Wine-Staging 11.1](https://www.phoronix.com/news/Wine-Staging-11.1)
  - [Phoronix: Adobe Photoshop work going upstream](https://www.phoronix.com/news/Adobe-Photoshop-Upstream-Wine)
  - [Phoronix: Wine 11.15 released](https://www.phoronix.com/news/Wine-11.15-Released)
  - [Tom's Hardware: Creative Cloud installers work thanks to HTML/JavaScript/XML fixes](https://www.tomshardware.com/software/linux/developer-patches-wine-to-make-photoshop-2021-and-2025-run-on-linux-adobe-creative-cloud-installers-finally-work-thanks-to-html-javascript-and-xml-fixes)
  - [PhialsBasement/wine-adobe-installers](https://github.com/PhialsBasement/wine-adobe-installers/releases)
  - Related merge requests: [ValveSoftware/wine#310](https://github.com/ValveSoftware/wine/pull/310),
    [wine!10025](https://gitlab.winehq.org/wine/wine/-/merge_requests/10025)

## Community projects

| Project | What it is | Relation to tuval |
|---|---|---|
| [AdobeWine](https://github.com/le-birnes/AdobeWine) (LGPL-2.1) | A Wine 11.18 build with a large Adobe patch set, DXVK and vkd3d-proton, plus scripts; tested mainly on NVIDIA | tuval's preferred runtime and the source of most current status reports. tuval does **not** adopt its "sign-in fix", which renames `NGL/adobe_licensing_wf.exe` (TUV102). |
| [AdobeOnLinux](https://github.com/qwertychouskie/AdobeOnLinux) | Guides and scripts; documents Wine-Staging ≥ 11.10 as the minimum | Source of the `min_wine = "11.10"` baseline |
| VM-based approaches ([The Register overview](https://www.theregister.com/software/2026/02/14/contain-your-windows-apps-inside-linux-windows/4334445)) | Run Windows itself in a VM and show its apps on the Linux desktop | Model for the planned fallback for `broken` apps |
| [WineHQ AppDB: Lightroom](https://appdb.winehq.org/objectManager.php?sClass=application&iId=5839) | User reports | Evidence for Lightroom Classic's `broken` tier |

## Gaps tuval fills

1. **Safety:** a machine-enforced guarantee that nothing touches licensing, plus an
   audit for prefixes that someone else's script may have modified.
2. **Honesty:** status tiers with evidence that the schema enforces. Popular claims of
   "works perfectly" usually come from a single GPU vendor.
3. **Diagnostics:** `tuval doctor` explains why a machine is or is not ready, before the
   user spends an hour on an installer.
4. **Distribution:** packaging beyond one distro, including a NixOS module.
