"""TradingView-style multi-select filter dropdown for the News space.

Thin wrapper over the shared :class:`dropdowns.FilterPill` (multi-select): a pill button
("Provider ▾" / "Provider (3) ▾") that opens the shared dark checklist popover (header,
search, checkbox rows, Select-all). Kept as a named class so the News space's imports and
usages stay unchanged. Empty selection == no constraint (all).
"""

from __future__ import annotations

from . import dropdowns


class MultiSelectFilter(dropdowns.FilterPill):
    """A TV-style pill that opens the shared multi-select checklist popover."""

    def __init__(self, label: str, options: list[str], parent=None):
        super().__init__(label, options, mode="multi", parent=parent)
