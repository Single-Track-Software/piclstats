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
    missing = [a for a in assets if not any(fnmatch.fnmatch(a, pat) for pat in patterns)]
    assert not missing, f"not shipped by package-data: {missing}"
