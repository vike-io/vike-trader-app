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
                  interval: str | None = None, *, interval_group: int | None = None) -> None:
        """Push symbol on the SYMBOL channel and interval on the INTERVAL channel.

        ``group`` is the symbol-link colour; ``interval_group`` is the (independent) interval-link
        colour. When ``interval_group`` is omitted it defaults to ``group`` — so a single colour
        still links symbol AND interval together (legacy/TradingView-style sync). Setting a
        *different* interval colour gives MultiCharts-style independent channels: a chart can
        follow another chart's timeframe without following its symbol, and vice-versa.

        Members carry ``link_group`` (symbol) and optionally ``interval_link_group`` (interval); a
        member without the latter falls back to ``link_group`` so single-channel members keep
        syncing interval on their symbol colour. ``symbol``/``interval`` are individually optional
        (a watchlist click sends symbol only). No-op while already broadcasting, or when both
        channels are unlinked, or when there is nothing to send."""
        if self._broadcasting:
            return
        if symbol is None and interval is None:
            return
        igroup = group if interval_group is None else interval_group
        if not group and not igroup:
            return
        self._broadcasting = True
        try:
            for member in list(self._members):
                if member is source:
                    continue
                sym = symbol if (symbol is not None and group
                                 and getattr(member, "link_group", 0) == group) else None
                m_igroup = getattr(member, "interval_link_group", None)
                if m_igroup is None:                       # legacy member -> symbol colour
                    m_igroup = getattr(member, "link_group", 0)
                itv = interval if (interval is not None and igroup
                                   and m_igroup == igroup) else None
                if sym is not None or itv is not None:
                    member.apply_link(sym, itv)
        finally:
            self._broadcasting = False
