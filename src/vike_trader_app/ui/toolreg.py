"""Tool registry for the empty-workspace re-architecture.

Maps a stable tool *key* (``screener``, ``journal``, …) to a factory that builds a fresh
instance of that tool widget, and wraps any tool widget in an ADS ``CDockWidget`` so it can
be docked/floated/torn-out on demand. The 7 non-chart tools (plus Studio) used to live as
fixed tabs of the ``SpaceDeck``; they now open lazily as dock widgets.

Factories take the ``MainWindow`` (``win``) so a tool can reach shared state (e.g. the data
manager's pins path). Imports are LAZY (local, inside ``factories()``) to avoid an
app↔tool import cycle and to keep ``import toolreg`` cheap.
"""

from __future__ import annotations

import logging
from typing import Callable

import PySide6QtAds as QtAds

log = logging.getLogger(__name__)


class ToolRegistry:
    """Static registry of tool key -> factory(win) -> widget."""

    # Stable tool keys, mirrored by ``factories()``. Kept as a constant so ``keys()``
    # can be called WITHOUT triggering the heavy lazy imports in ``factories()``
    # (the rail builder calls ``keys()`` at startup just to draw buttons).
    # A drift guard test asserts ``set(_KEYS) == set(factories())``.
    _KEYS: tuple[str, ...] = (
        "screener", "journal", "alerts", "data",
        "news", "calendar", "options", "studio",
    )

    @staticmethod
    def factories() -> dict[str, Callable]:
        # Lazy local imports: break the app<->tool import cycle, keep `import toolreg` cheap.
        from .alerts import AlertsTab
        from .datamanager import DataManagerTab
        from .economic_calendar import EconomicCalendarTab
        from .equity_calendar import CalendarSpace
        from .journal import JournalTab
        from .news import NewsTab
        from .options_tab import OptionsTab
        from .screener import ScreenerTab
        # Studio's factory delegates to MainWindow._build_studio_widget (it needs the window's
        # pipeline wiring), so StudioTab is NOT imported here.

        def _data(win):
            # DataManagerTab defaults pins_path internally (datamanager._PINS_PATH); pass through
            # whatever the window exposes (None today) so a future task can wire win._pins_path.
            return DataManagerTab(pins_path=getattr(win, "_pins_path", None))

        def _calendar(win):
            # app.py: EconomicCalendarTab() mounted inside CalendarSpace(economic_tab=…).
            return CalendarSpace(economic_tab=EconomicCalendarTab())

        return {
            "screener": lambda win: ScreenerTab(),
            "journal": lambda win: JournalTab(),
            "alerts": lambda win: AlertsTab(),
            "data": _data,
            "news": lambda win: NewsTab(),
            "calendar": _calendar,
            "options": lambda win: OptionsTab(),
            # Studio is the 8th on-demand tool. Its widget is the full StudioTab WITH its lockstep
            # chart block (studio_price + replay controls) mounted; MainWindow._build_studio_widget
            # builds that and sets win.studio / win.studio_price (the close handler nils them).
            "studio": lambda win: win._build_studio_widget(),
        }

    @staticmethod
    def keys() -> list[str]:
        return list(ToolRegistry._KEYS)

    @staticmethod
    def create(key: str, win):
        try:
            return ToolRegistry.factories()[key](win)
        except KeyError:
            raise KeyError(
                f"{key!r} not in ToolRegistry; valid keys: {list(ToolRegistry._KEYS)}"
            )


TOOL_LABELS = {
    "screener": "Screener",
    "journal": "Journal",
    "alerts": "Alerts",
    "data": "Data",
    "news": "News",
    "calendar": "Calendar",
    "options": "Options",
    "studio": "Studio",
}

# Distinct per-tool launcher colours (rail + top-bar), so each tool reads at a glance like a
# colourful competitor toolbar instead of one uniform accent. Kept HERE (not in theme.py — the
# theme palette is off-limits) but chosen to sit comfortably in the dark theme. studio/chart stay
# in the green accent family (they're the SPACE/primary actions, not on-demand tools).
TOOL_COLORS = {
    "screener": "#4c8dff",   # blue
    "journal": "#b07cf0",    # violet
    "alerts": "#ff5c5c",     # red
    "data": "#36c5a8",       # teal
    "news": "#f0a500",       # amber
    "calendar": "#5cc46b",   # green
    "options": "#e85aad",    # pink
    "studio": "#3ddc97",     # green accent family
    "chart": "#7aa2f7",      # soft blue (the chart space / New-chart action)
}


def tool_color(key: str, fallback: str) -> str:
    """Resting/active launcher colour for a tool ``key`` (``fallback`` when not mapped)."""
    return TOOL_COLORS.get(key, fallback)


def tool_hover_color(key: str, fallback: str) -> str:
    """A lightened hover variant of the tool colour (no Qt import — plain hex maths)."""
    base = TOOL_COLORS.get(key)
    if not base:
        return fallback
    base = base.lstrip("#")
    try:
        r, g, b = (int(base[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return fallback
    # blend ~40% toward white for a brighter hover cue
    r = int(r + (255 - r) * 0.40)
    g = int(g + (255 - g) * 0.40)
    b = int(b + (255 - b) * 0.40)
    return f"#{r:02x}{g:02x}{b:02x}"


def make_tool_dock(manager, key: str, widget, icon=None) -> "QtAds.CDockWidget":
    """Wrap a tool widget in a closable/floatable dock that is destroyed when closed.

    Mirrors ``dockshell.make_panel_dock``'s construction (ForceNoScrollArea, default
    features) but adds ``DockWidgetDeleteOnClose`` so a re-opened tool is rebuilt fresh
    from its factory, and namespaces the objectName as ``tool:<key>``.
    """
    dock = QtAds.CDockWidget(manager, TOOL_LABELS.get(key, key))
    dock.setObjectName(f"tool:{key}")
    dock.setWidget(widget, QtAds.CDockWidget.ForceNoScrollArea)
    dock.setFeatures(
        QtAds.CDockWidget.DefaultDockWidgetFeatures
        | QtAds.CDockWidget.DockWidgetPinnable
        | QtAds.CDockWidget.DockWidgetDeleteOnClose
    )
    if icon is not None:
        dock.setIcon(icon)
    return dock
