"""Every template and static file must be covered by the wheel's package-data.

The container installs the package with pip, so a file the templates reference
but pyproject's package-data does not name (the brand SVGs, first shipped
2026-09-23) is silently absent in production and 404s.
"""

import fnmatch
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "piclstats" / "web"


def _glob_matches(pattern: str, path: str) -> bool:
    """setuptools globs: `*` never crosses a directory, unlike fnmatch's."""
    pat_parts, path_parts = pattern.split("/"), path.split("/")
    return len(pat_parts) == len(path_parts) and all(
        fnmatch.fnmatch(part, pat) for part, pat in zip(path_parts, pat_parts)
    )


def test_glob_star_does_not_cross_directories():
    assert _glob_matches("templates/*.html", "templates/home.html")
    assert not _glob_matches("templates/*.html", "templates/timing/station.html")


def test_every_web_asset_matches_a_package_data_pattern():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = pyproject["tool"]["setuptools"]["package-data"]["piclstats.web"]
    assets = [
        p.relative_to(WEB).as_posix()
        for sub in ("templates", "static")
        for p in (WEB / sub).rglob("*")
        if p.is_file()
    ]
    assert assets, "no web assets found"
    missing = [a for a in assets if not any(_glob_matches(pat, a) for pat in patterns)]
    assert not missing, f"not shipped by package-data: {missing}"
