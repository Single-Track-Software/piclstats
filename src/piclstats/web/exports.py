"""Helpers for files the site hands out (CSV downloads): safe cells and names."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# A cell starting with one of these is a formula to Excel and Google Sheets.
# Rider and team names come from an outside source (raceresult), so a name
# like "=HYPERLINK(...)" must land in a coach's spreadsheet as text.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")


def csv_safe(value: Any) -> Any:
    """Neutralise a would-be spreadsheet formula; numbers and None pass through."""
    if isinstance(value, str) and value.startswith(_FORMULA_LEADERS):
        return "'" + value
    return value


def csv_safe_row(row: Iterable[Any]) -> list[Any]:
    return [csv_safe(v) for v in row]


def filename_part(text: str, limit: int = 40) -> str:
    """A fragment of a download filename from user text: letters, digits, _ and - only."""
    return _UNSAFE_NAME.sub("_", text).strip("_")[:limit] or "x"
