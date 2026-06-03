"""Tests for the theme's pure helpers (HSL authoring + sign coloring) and token wiring."""

from vike_trader_app.ui import theme


def test_hsl_primary_anchors():
    # Binding-independent, exact HSL -> hex conversions.
    assert theme.hsl(0, 0, 0) == "#000000"
    assert theme.hsl(0, 0, 100) == "#ffffff"
    assert theme.hsl(0, 100, 50) == "#ff0000"
    assert theme.hsl(120, 100, 50) == "#00ff00"
    assert theme.hsl(240, 100, 50) == "#0000ff"


def test_hsl_returns_hex_string():
    for tok in (theme.BG, theme.SURFACE, theme.HOVER, theme.BORDER, theme.TEXT):
        assert tok.startswith("#") and len(tok) == 7
        int(tok[1:], 16)  # parses as hex


def test_surface_anchors_match_palette():
    # The locked GitHub-dark blue ladder.
    assert theme.BG == "#0d1117"
    assert theme.SURFACE == "#161b22"
    assert theme.HOVER == "#21252c"  # exact hsl(215,15,15); ~Primer #21262d


def test_aliases_collapse_onto_canonical_tokens():
    # The 9 old surface/border tokens hold only the 4 distinct values now.
    assert theme.CHART_BG == theme.BG
    assert theme.ROW_ODD == theme.BG
    assert theme.PANEL == theme.SURFACE
    assert theme.PANEL2 == theme.SURFACE
    assert theme.RAISE == theme.SURFACE
    assert theme.BORDER2 == theme.BORDER
    surfaces = {theme.BG, theme.SURFACE, theme.HOVER, theme.BORDER}
    assert len(surfaces) == 4


def test_color_for_sign():
    assert theme.color_for(12.5) == theme.UP
    assert theme.color_for(-3.0) == theme.DOWN
    assert theme.color_for(0) == theme.TEXT2


def test_stylesheet_renders_without_placeholders():
    qss = theme.stylesheet()
    # No leftover f-string token placeholders.
    for name in ("{BG}", "{SURFACE}", "{HOVER}", "{BORDER}", "{ACCENT}", "{ON_ACCENT}"):
        assert name not in qss
    assert theme.BG in qss and theme.SURFACE in qss and theme.ACCENT in qss
    assert "QPushButton" in qss
