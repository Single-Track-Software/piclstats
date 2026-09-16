"""Blocking keys for rider and team identity.

`name_key` folds the variants that are the same person spelled differently
(O'REILLY / OREILLY, LA LONDE / LALONDE, accents, case) into one string so
merging can block on it. It is stored next to the raw name, never in place
of it. The Alembic backfill uses the same rule in SQL minus accent folding,
so re-run `piclstats dq keys` after a migration if accented names matter.
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_WS = re.compile(r"\s+")


def _ascii(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def name_key(name: str) -> str:
    """Upper-case ASCII letters and digits only: "Neve O'Reilly" -> "NEVEOREILLY"."""
    return _NON_ALNUM.sub("", _ascii(name).upper())


def team_key(team: str | None) -> str | None:
    """Lower-case with whitespace collapsed: "PGH  north " -> "pgh north"."""
    if team is None:
        return None
    key = _WS.sub(" ", _ascii(team)).strip().lower()
    return key or None
