from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.helpers import write
from tuval.compliance import check_profile
from tuval.profiles import (
    InstallMethod,
    Profile,
    ProfileError,
    RegistryType,
    Tier,
    index_profiles,
    is_https_url,
    load_bundled_profiles,
    load_profile_file,
    parse_profile,
)
from tuval.version import Version

# A raw string so the TOML literal strings below keep their single backslashes.
VALID = r"""
schema = 1
id = "demo"
name = "Demo App"
vendor = "Example"
prefix = "creative-cloud"
installed_via = "creative-cloud"
exe = "Program Files/Example/Demo/Demo.exe"
min_wine = "11.10"
requires = []
mime_types = ["image/x-demo"]

[wine]
windows_version = "win10"
winetricks = ["corefonts"]

[wine.dll_overrides]
d3d11 = "builtin"

[wine.env]
DXVK_LOG_LEVEL = "none"

[[wine.registry]]
key = 'HKCU\Software\Wine\X11 Driver'
name = "Decorated"
type = "REG_SZ"
data = "N"
why = "Undecorated windows keep panels drawn."

[[wine.registry]]
key = 'HKCU\Software\Wine\WineDbg'
name = "ShowCrashDialog"
type = "REG_DWORD"
data = 0
why = "No crash dialog."

[status]
tier = "silver"
verified_by_us = false
tested_gpus = ["nvidia"]
evidence = ["https://example.org/report"]
notes = "Works for the reporter."

[[known_issues]]
summary = "Something is slow"
source = "https://example.org/issues/1"
"""

BUNDLED_IDS = {
    "creative-cloud",
    "photoshop",
    "illustrator",
    "premiere-pro",
    "after-effects",
    "audition",
    "media-encoder",
    "indesign",
    "lightroom-classic",
}


def mutated(old: str, new: str) -> str:
    assert old in VALID, f"mutation target {old!r} is not in the fixture"
    return VALID.replace(old, new, 1)


def demo_profile() -> Profile:
    return parse_profile(VALID, "demo.toml")


def test_valid_profile_parses_every_field() -> None:
    profile = demo_profile()
    assert profile.id == "demo"
    assert profile.installed_via is InstallMethod.CREATIVE_CLOUD
    assert profile.min_wine == Version(11, 10)
    assert profile.mime_types == ("image/x-demo",)
    assert profile.wine.windows_version == "win10"
    assert profile.wine.winetricks == ("corefonts",)
    assert profile.wine.dll_overrides == {"d3d11": "builtin"}
    assert profile.wine.env == {"DXVK_LOG_LEVEL": "none"}
    decorated, crash_dialog = profile.wine.registry
    assert decorated.key == "HKCU\\Software\\Wine\\X11 Driver"
    assert decorated.data == "N"
    assert crash_dialog.kind is RegistryType.REG_DWORD
    assert crash_dialog.data == 0
    assert profile.status.tier is Tier.SILVER
    assert profile.status.tested_gpus == ("nvidia",)
    assert profile.known_issues[0].source == "https://example.org/issues/1"
    assert profile.source == "demo.toml"


def test_optional_sections_default_to_empty() -> None:
    text = "\n".join(
        [
            "schema = 1",
            'id = "bare"',
            'name = "Bare"',
            'vendor = "Example"',
            'prefix = "creative-cloud"',
            'installed_via = "standalone-installer"',
            'exe = "Bare.exe"',
            'min_wine = "11.10"',
            "[status]",
            'tier = "untested"',
            "verified_by_us = false",
        ]
    )
    profile = parse_profile(text)
    assert profile.wine.registry == ()
    assert profile.wine.windows_version is None
    assert profile.status.evidence == ()
    assert profile.known_issues == ()


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("schema = 1", "schema = 2", "unsupported schema"),
        ("schema = 1", "schema = true", "must be int, got bool"),
        ('name = "Demo App"', 'name = "Demo App"\ncolour = "red"', "unknown key"),
        ('name = "Demo App"', 'name = "  "', "must not be empty"),
        ('id = "demo"', 'id = "Demo_App"', "does not match"),
        ('installed_via = "creative-cloud"', 'installed_via = "torrent"', "must be one of"),
        ('min_wine = "11.10"', 'min_wine = "latest"', "must look like 11.10"),
        ('mime_types = ["image/x-demo"]', 'mime_types = ["Image"]', "does not match"),
        ("requires = []", "requires = [1]", "must be a string"),
        ('exe = "Program Files/Example/Demo/Demo.exe"', 'exe = "/opt/Demo.exe"', "relative"),
        ('exe = "Program Files/Example/Demo/Demo.exe"', r"exe = 'Example\Demo.exe'", "relative"),
        ('exe = "Program Files/Example/Demo/Demo.exe"', 'exe = "C:/Demo.exe"', "relative"),
        ('exe = "Program Files/Example/Demo/Demo.exe"', 'exe = "a/../../Demo.exe"', "relative"),
        ('exe = "Program Files/Example/Demo/Demo.exe"', 'exe = "Demo.sh"', "must end in .exe"),
        ('windows_version = "win10"', 'windows_version = "winxp"', "windows_version"),
        ('winetricks = ["corefonts"]', 'winetricks = ["core fonts"]', "does not match"),
        ('d3d11 = "builtin"', 'd3d11 = "sometimes"', "invalid dll override"),
        ('DXVK_LOG_LEVEL = "none"', 'dxvk_log_level = "none"', "environment variable"),
        ("key = 'HKCU\\Software\\Wine\\WineDbg'", "key = 'Software\\Wine'", "HKCU"),
        ('type = "REG_SZ"', 'type = "REG_BINARY"', "must be one of"),
        ("data = 0", "data = 4294967296", "32 bits"),
        ("data = 0", "data = -1", "32 bits"),
        ("data = 0", "data = false", "got bool"),
        ('why = "No crash dialog."', 'why = ""', "must not be empty"),
        ('tier = "silver"', 'tier = "gold"', "requires verified_by_us"),
        ('tier = "silver"', 'tier = "platinum"', "requires verified_by_us"),
        ("verified_by_us = false", "verified_by_us = 0", "must be bool"),
        ('tested_gpus = ["nvidia"]', 'tested_gpus = ["voodoo"]', "unknown GPU vendor"),
        ('evidence = ["https://example.org/report"]', "evidence = []", "needs at least one"),
        ('evidence = ["https://example.org/report"]', 'evidence = ["http://x.org"]', "https"),
        ('evidence = ["https://example.org/report"]', 'evidence = ["https://"]', "https"),
        ('source = "https://example.org/issues/1"', 'source = "a forum post"', "https"),
        ('notes = "Works for the reporter."', 'notes = "x"\nscore = 9', "unknown key"),
        ("[status]", "[state]", "missing required key 'status'"),
        ("schema = 1", "schema = = 1", "invalid TOML"),
    ],
)
def test_invalid_profiles_are_rejected(old: str, new: str, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        parse_profile(mutated(old, new))


def test_untested_profile_cannot_claim_verification() -> None:
    text = mutated('tier = "silver"', 'tier = "untested"').replace(
        "verified_by_us = false", "verified_by_us = true"
    )
    with pytest.raises(ProfileError, match="untested profile cannot be verified_by_us"):
        parse_profile(text)


def test_gold_is_allowed_once_verified_by_us() -> None:
    text = mutated('tier = "silver"', 'tier = "gold"').replace(
        "verified_by_us = false", "verified_by_us = true"
    )
    assert parse_profile(text).status.tier is Tier.GOLD


def test_untested_profile_needs_no_evidence() -> None:
    text = mutated('tier = "silver"', 'tier = "untested"').replace(
        'evidence = ["https://example.org/report"]', "evidence = []"
    )
    assert parse_profile(text).status.evidence == ()


def test_errors_name_the_offending_table() -> None:
    with pytest.raises(ProfileError, match=r"demo\.toml\.wine\.registry\[1\]"):
        parse_profile(mutated("data = 0", "data = -1"), "demo.toml")


def test_load_profile_file_requires_id_to_match_file_name(tmp_path: Path) -> None:
    path = write(tmp_path / "other.toml", VALID)
    with pytest.raises(ProfileError, match="must match file name 'other'"):
        load_profile_file(path)


def test_load_profile_file_reads_matching_file(tmp_path: Path) -> None:
    path = write(tmp_path / "demo.toml", VALID)
    assert load_profile_file(path).source == str(path)


def test_load_profile_file_reports_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="cannot read"):
        load_profile_file(tmp_path / "missing.toml")


def test_index_profiles_rejects_duplicates() -> None:
    profile = demo_profile()
    with pytest.raises(ProfileError, match="duplicate profile id 'demo'"):
        index_profiles([profile, profile])


@pytest.mark.parametrize("requirement", ["demo", "nowhere"])
def test_index_profiles_rejects_bad_requirements(requirement: str) -> None:
    profile = replace(demo_profile(), requires=(requirement,))
    with pytest.raises(ProfileError, match="invalid requirement"):
        index_profiles([profile])


def test_index_profiles_accepts_satisfied_requirements() -> None:
    base = replace(demo_profile(), id="base")
    app = replace(demo_profile(), requires=("base",))
    assert set(index_profiles([app, base])) == {"base", "demo"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/le-birnes/AdobeWine", True),
        ("http://github.com", False),
        ("https://", False),
        ("https://exa mple.org", False),
        ("ftp://example.org", False),
        ("", False),
    ],
)
def test_is_https_url(url: str, expected: bool) -> None:
    assert is_https_url(url) is expected


def test_bundled_profiles_load() -> None:
    assert set(load_bundled_profiles()) == BUNDLED_IDS


@pytest.mark.parametrize("profile_id", sorted(BUNDLED_IDS))
def test_bundled_profile_is_compliant(profile_id: str) -> None:
    profile = load_bundled_profiles()[profile_id]
    assert check_profile(profile) == []


def test_bundled_apps_share_the_creative_cloud_prefix() -> None:
    profiles = load_bundled_profiles()
    for profile in profiles.values():
        assert profile.prefix == "creative-cloud"
        expected = () if profile.id == "creative-cloud" else ("creative-cloud",)
        assert profile.requires == expected, profile.id


def test_no_bundled_profile_claims_self_verification_yet() -> None:
    # Nothing has been tested on tuval's own hardware; every tier is borrowed evidence.
    assert not any(p.status.verified_by_us for p in load_bundled_profiles().values())
