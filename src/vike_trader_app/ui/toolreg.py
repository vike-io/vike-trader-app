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

# Pinned (symbol, interval) series kept precomputed — mirrors app.py's module-level _PINS_PATH.
_PINS_PATH = "storage/pins.json"


class ToolRegistry:
    """Static registry of tool key -> factory(win) -> widget."""

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
        from .studio import StudioTab

        def _data(win):
            # app.py builds DataManagerTab(pins_path=_PINS_PATH); prefer the live window's
            # value if it exposes one, else fall back to the module default.
            pins_path = getattr(win, "_pins_path", None) or _PINS_PATH
            return DataManagerTab(pins_path=pins_path)

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
            # Bare StudioTab(): app.py also mounts a chart block via studio.mount_chart(...)
            # after construction — that integration is handled later by open_tool, not here.
            "studio": lambda win: StudioTab(),
        }

    @staticmethod
    def keys() -> list[str]:
        return list(ToolRegistry.factories().keys())

    @staticmethod
    def create(key: str, win):
        return ToolRegistry.factories()[key](win)


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
        | QtAds.CDockWidget.DockWidgetDeleteOnClose
    )
    if icon is not None:
        dock.setIcon(icon)
    return dock
