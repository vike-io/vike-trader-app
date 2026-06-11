"""Symbol link groups (Phase 3 of the workspace program).

MultiCharts/TradingView-style "link colors": charts (and the watchlist) tagged with the same
colour move together. Picking a symbol — or changing the interval — on one member broadcasts
the new (symbol, interval) to every OTHER member of the same colour. Group 0 = unlinked.

``SymbolLinkBus`` is deliberately Qt-free (plain Python, unit-tested) so the broadcast/guard
logic is verifiable without a widget; ``LinkDot`` is the small colour-swatch button surface.
Receiver members duck-type two attributes: ``link_group`` (int) and ``apply_link(symbol,
interval)``. Sources just call ``bus.broadcast(group, source, symbol, interval)``.
"""

from __future__ import annotations

# (group id, colour hex, label). id 0 is the unlinked state (hollow grey dot, no broadcast).
LINK_GROUPS: list[tuple[int, str, str]] = [
    (0, "#6e7681", "None"),
    (1, "#f85149", "Red"),
    (2, "#3fb950", "Green"),
    (3, "#58a6ff", "Blue"),
    (4, "#f0883e", "Orange"),
    (5, "#d29922", "Yellow"),
    (6, "#a855f7", "Purple"),
]
LINK_COLOR = {gid: color for gid, color, _name in LINK_GROUPS}


class SymbolLinkBus:
    """Routes (symbol, interval) changes between members sharing a non-zero link group.

    Members register once via ``add_member``; each carries its own ``link_group`` (so a member
    can be recoloured without re-registering) and an ``apply_link(symbol, interval)`` slot.
    ``broadcast`` is re-entrancy guarded: a member's apply_link will itself emit a change, and
    that nested broadcast is suppressed so a linked pair can't ping-pong forever.
    """

    def __init__(self) -> None:
        self._members: list = []
        self._broadcasting = False

    def add_member(self, member) -> None:
        if member not in self._members:
            self._members.append(member)

    def remove_member(self, member) -> None:
        if member in self._members:
            self._members.remove(member)

    def broadcast(self, group: int, source, symbol: str | None = None,
                  interval: str | None = None) -> None:
        """Push (symbol, interval) to every member in ``group`` except ``source``.

        No-op for the unlinked group (0) or while already broadcasting. ``symbol``/``interval``
        are individually optional: a watchlist click sends symbol only (interval stays per
        chart); a chart change sends both (full TradingView-style sync)."""
        if not group or self._broadcasting:
            return
        if symbol is None and interval is None:
            return
        self._broadcasting = True
        try:
            for member in list(self._members):
                if member is source:
                    continue
                if getattr(member, "link_group", 0) == group:
                    member.apply_link(symbol, interval)
        finally:
            self._broadcasting = False
