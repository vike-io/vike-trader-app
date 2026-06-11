"""Chart-style icons (TradingView-style): every style has a distinct, non-empty glyph; the
toolbar button is icon-only and tracks the active style; every dropdown entry carries its icon."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui import chart_styles  # noqa: E402
from vike_trader_app.ui.chart import PriceChart  # noqa: E402
from vike_trader_app.ui.style_icons import style_icon  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _pixels(icon):
    """Set of opaque pixel coords of the icon's 36px frame — glyph fingerprint."""
    img = icon.pixmap(18, 18).toImage()
    return frozenset(
        (x, y) for y in range(img.height()) for x in range(img.width())
        if (img.pixel(x, y) >> 24) & 0xFF > 40
    )


def test_every_style_has_a_nonempty_icon(app):
    for s in chart_styles.ALL_STYLES:
        icon = style_icon(s)
        assert not icon.isNull(), s
        assert len(_pixels(icon)) > 10, f"{s}: glyph is (nearly) blank"


def test_glyphs_are_distinct(app):
    # every style's glyph must differ from every other (no copy-paste icons)
    prints = {s: _pixels(style_icon(s)) for s in chart_styles.ALL_STYLES}
    seen = {}
    for s, fp in prints.items():
        assert fp not in seen, f"{s} renders identically to {seen.get(fp)}"
        seen[fp] = s


def test_toolbar_button_is_icon_only_and_tracks_style(app):
    pc = PriceChart()
    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i)
            for i in range(30)]
    pc.set_data(bars, [])
    assert pc._style_btn.text() == ""              # icon-only (TradingView-style)
    assert not pc._style_btn.icon().isNull()
    before = _pixels(pc._style_btn.icon())
    pc.set_style("Renko")
    assert "Renko" in pc._style_btn.toolTip()
    assert _pixels(pc._style_btn.icon()) != before  # the glyph actually switched
    pc.deleteLater()


def test_every_menu_entry_has_its_icon(app):
    pc = PriceChart()
    menu = pc._style_btn.menu()
    labeled = [a for a in menu.actions() if a.text() in chart_styles.ALL_STYLES]
    assert len(labeled) == len(chart_styles.ALL_STYLES)
    for a in labeled:
        assert not a.icon().isNull(), a.text()
    pc.deleteLater()
