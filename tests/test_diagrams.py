"""SVG builders for the DQ page (pure)."""

from piclstats.quality.diagrams import merge_map_svg, pipeline_flow_svg, sparkline_svg


def test_flow_escapes_labels_and_draws_arrows():
    svg = pipeline_flow_svg([("A<b>", "1", "x", "#000"), ("B", "2", "y", "#111")])
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "A&lt;b&gt;" in svg and "<b>" not in svg
    assert svg.count("<line") == 1 and svg.count("<rect") == 2


def test_merge_map_caps_variants_and_escapes():
    edges = [{"raw_value": f"V{i} <x>", "mechanism": "casing", "volume": i} for i in range(20)]
    svg = merge_map_svg("O'Neil & Co", edges, 190)
    assert "+6 more variant(s)" in svg
    assert "&lt;x&gt;" in svg and "O&#x27;Neil &amp; Co" in svg
    assert svg.count("<path") == 14 + 1  # one per shown variant, one per mechanism


def test_sparkline_needs_two_points():
    assert sparkline_svg([1.0]) == ""
    assert "<polyline" in sparkline_svg([1.0, None, 3.0])
