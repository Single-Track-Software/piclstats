"""The committed Tailwind build must cover every class the templates use.

Tailwind only emits rules for classes it finds in the templates, and the
output is committed rather than built in the container. A template edit that
adds a class without running scripts/build_css.sh would ship unstyled, so this
test compares the two.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "piclstats" / "web"

# Tailwind escapes these characters in selectors.
_ESCAPE = re.compile(r"([:/.\[\]#%(),!'\"=+*&~^$@])")

# Class-like tokens that are not Tailwind utilities (chart.js / semantic hooks).
_NOT_TAILWIND = {"inline", "list-none"}


def _template_classes() -> set[str]:
    found: set[str] = set()
    for path in WEB.glob("templates/**/*.html"):
        for attr in re.findall(r'class="([^"]*)"', path.read_text()):
            # Drop Jinja expressions; only literal tokens can be checked.
            attr = re.sub(r"{{.*?}}|{%.*?%}", " ", attr)
            found.update(t for t in attr.split() if re.fullmatch(r"[\w:/.\[\]#%()-]+", t))
        for quoted in re.findall(r"'((?:[\w:/.-]+ )*[\w:/.-]+)'", path.read_text()):
            # 'text-green-600' if ... else 'text-red-500'
            found.update(t for t in quoted.split() if "-" in t)
    return found


def _built_selectors(css: str) -> set[str]:
    return set(re.findall(r"\.((?:\\.|[\w-])+)", css))


def test_every_template_class_is_built():
    css = (WEB / "static" / "app.css").read_text()
    built = _built_selectors(css)
    missing = sorted(
        c
        for c in _template_classes()
        if _ESCAPE.sub(r"\\\1", c) not in built
        and c not in _NOT_TAILWIND
        and "-" in c
        and not c.endswith("-")  # prefix left behind by a stripped Jinja expression
    )
    assert not missing, f"run scripts/build_css.sh; unbuilt classes: {missing}"
