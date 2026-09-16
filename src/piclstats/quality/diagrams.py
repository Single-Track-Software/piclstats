"""Inline-SVG builders for the admin DQ page, ported from gvpd's routes.py.

Plain string assembly: fixed geometry, every label escaped, no chart library.
Light-theme colours to match the rest of the site.
"""

from __future__ import annotations

import html
from typing import Any

MECHANISM_COLORS = {
    "exact": "#6b7280",
    "casing": "#3b82f6",
    "whitespace": "#14b8a6",
    "punctuation": "#8b5cf6",
    "alias": "#6366f1",
    "pattern": "#22c55e",
    "typo": "#f59e0b",
    "manual": "#ef4444",
}
GRAY = "#6b7280"
TEXT = "#111827"


def mechanism_color(mechanism: str) -> str:
    return MECHANISM_COLORS.get(mechanism, GRAY)


def pipeline_flow_svg(nodes: list[tuple[str, str, str, str]]) -> str:
    """A row of boxes (title, big value, sub-label, colour) joined by arrows."""
    W, H, nw, nh = 980, 150, 124, 72
    gap = (W - nw) / (len(nodes) - 1) if len(nodes) > 1 else 0
    y = (H - nh) / 2
    xs = [round(i * gap, 1) for i in range(len(nodes))]
    p = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="ui-monospace,monospace" role="img" '
        f'aria-label="Pipeline flow">',
        '<defs><marker id="ah" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">'
        f'<path d="M0,0 L6,3 L0,6 Z" fill="{GRAY}"/></marker></defs>',
    ]
    cy = y + nh / 2
    for i in range(len(nodes) - 1):
        p.append(
            f'<line x1="{xs[i] + nw}" y1="{cy}" x2="{xs[i + 1] - 3}" y2="{cy}" '
            f'stroke="{GRAY}" stroke-width="1.5" marker-end="url(#ah)"/>'
        )
    for (title, big, sub, color), x in zip(nodes, xs):
        p.append(
            f'<rect x="{x}" y="{y}" width="{nw}" height="{nh}" rx="8" fill="#ffffff" '
            f'stroke="{color}" stroke-width="1.5"/>'
        )
        p.append(
            f'<text x="{x + nw / 2}" y="{y + 18}" fill="{GRAY}" font-size="9.5" '
            f'text-anchor="middle">{html.escape(title)}</text>'
        )
        p.append(
            f'<text x="{x + nw / 2}" y="{y + 43}" fill="{color}" font-size="17" '
            f'font-weight="bold" text-anchor="middle">{html.escape(big)}</text>'
        )
        p.append(
            f'<text x="{x + nw / 2}" y="{y + 61}" fill="{GRAY}" font-size="9.5" '
            f'text-anchor="middle">{html.escape(sub)}</text>'
        )
    p.append("</svg>")
    return "".join(p)


def merge_map_svg(canonical: str, edges: list[dict[str, Any]], total_volume: int) -> str:
    """Sankey-style map: raw variants -> mechanism -> canonical."""
    shown = edges[:14]
    extra = len(edges) - len(shown)
    n = len(shown)
    rh, pad, W = 44, 28, 980
    var_x, var_w = 20, 320
    mech_x, mech_w = 470, 150
    canon_x, canon_w = 760, 200
    mechs: list[str] = []
    for e in shown:
        if e["mechanism"] not in mechs:
            mechs.append(e["mechanism"])
    H = max(n, len(mechs), 1) * rh + pad * 2

    def center_ys(count: int) -> list[float]:
        top = (H - count * rh) / 2 + rh / 2
        return [round(top + i * rh, 1) for i in range(count)]

    var_ys = center_ys(n)
    mech_y = {m: y for m, y in zip(mechs, center_ys(len(mechs)))}
    canon_y = round(H / 2, 1)
    p = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="ui-monospace,monospace" role="img" '
        f'aria-label="Merge map for {html.escape(canonical)}">',
        f'<text x="{var_x}" y="16" fill="{GRAY}" font-size="11">RAW VARIANTS</text>',
        f'<text x="{mech_x}" y="16" fill="{GRAY}" font-size="11">MECHANISM</text>',
        f'<text x="{canon_x}" y="16" fill="{GRAY}" font-size="11">CANONICAL</text>',
    ]
    for e, vy in zip(shown, var_ys):
        my = mech_y[e["mechanism"]]
        x1, x2 = var_x + var_w, mech_x
        dx = (x2 - x1) * 0.5
        p.append(
            f'<path d="M {x1} {vy} C {x1 + dx} {vy}, {x2 - dx} {my}, {x2} {my}" '
            f'stroke="{mechanism_color(e["mechanism"])}" stroke-width="1.5" fill="none" opacity="0.6"/>'
        )
    for m in mechs:
        my = mech_y[m]
        x1, x2 = mech_x + mech_w, canon_x
        dx = (x2 - x1) * 0.5
        p.append(
            f'<path d="M {x1} {my} C {x1 + dx} {my}, {x2 - dx} {canon_y}, {x2} {canon_y}" '
            f'stroke="#6366f1" stroke-width="1.5" fill="none" opacity="0.5"/>'
        )
    for e, vy in zip(shown, var_ys):
        sc = mechanism_color(e["mechanism"])
        label = str(e["raw_value"])
        label = label[:36] + "…" if len(label) > 37 else label
        p.append(
            f'<rect x="{var_x}" y="{vy - 15}" width="{var_w}" height="30" rx="6" fill="#ffffff" stroke="{sc}"/>'
        )
        p.append(
            f'<text x="{var_x + 10}" y="{vy + 4}" fill="{TEXT}" font-size="12">{html.escape(label)}</text>'
        )
        p.append(
            f'<text x="{var_x + var_w - 8}" y="{vy + 4}" fill="{GRAY}" font-size="11" '
            f'text-anchor="end">{int(e["volume"] or 0):,}</text>'
        )
    for m in mechs:
        my = mech_y[m]
        p.append(
            f'<rect x="{mech_x}" y="{my - 15}" width="{mech_w}" height="30" rx="6" fill="#f3f4f6" stroke="#d1d5db"/>'
        )
        p.append(
            f'<text x="{mech_x + mech_w / 2}" y="{my + 4}" fill="{mechanism_color(m)}" font-size="11.5" '
            f'text-anchor="middle" font-weight="600">{html.escape(m)}</text>'
        )
    clabel = canonical if len(canonical) <= 24 else canonical[:23] + "…"
    p.append(
        f'<rect x="{canon_x}" y="{canon_y - 22}" width="{canon_w}" height="44" rx="8" fill="#ecfdf5" stroke="#16a34a"/>'
    )
    p.append(
        f'<text x="{canon_x + canon_w / 2}" y="{canon_y - 2}" fill="#15803d" font-size="13" '
        f'text-anchor="middle" font-weight="bold">{html.escape(clabel)}</text>'
    )
    p.append(
        f'<text x="{canon_x + canon_w / 2}" y="{canon_y + 15}" fill="{GRAY}" font-size="10.5" '
        f'text-anchor="middle">{len(edges)} raw · {total_volume:,} rows</text>'
    )
    if extra > 0:
        p.append(
            f'<text x="{var_x}" y="{H - 6}" fill="{GRAY}" font-size="11">+{extra} more variant(s) not shown</text>'
        )
    p.append("</svg>")
    return "".join(p)


def sparkline_svg(values: list[float | None], color: str = "#6366f1") -> str:
    pts = [v for v in values if v is not None]
    if len(pts) < 2:
        return ""
    W, H = 120, 28
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    coords = []
    step = W / (len(values) - 1)
    for i, v in enumerate(values):
        if v is None:
            continue
        coords.append(f"{round(i * step, 1)},{round(H - 3 - (v - lo) / span * (H - 6), 1)}")
    return (
        f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
        f'<polyline points="{" ".join(coords)}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>'
    )
