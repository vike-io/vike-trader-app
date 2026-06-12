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
