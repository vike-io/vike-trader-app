"""Client-side emulated stop/trailing orders for the live path.

No venue honors native stops, so vike monitors each CLOSED bar and fires a MARKET order when the
trigger crosses. Trigger logic is core.orders.order_fill_price — the SAME function the backtest fill
model uses — so live emulation matches backtest by construction. FIDELITY GAP: this is bar-close
driven, not intra-bar; a wick that reverses still fires on the closing bar and the market fills at the
next price (vs the backtest's adverse-but-specific max(price, bar.open) fill).
"""

from ..core.orders import Order, order_fill_price


class ConditionalBook:
    def __init__(self) -> None:
        self._orders: list[Order] = []

    def add_stop(self, side: int, size: float, price: float, weight: float = 0.0) -> None:
        self._orders.append(Order("stop", side, size, price=price, weight=weight))

    def add_trailing(self, side: int, size: float, trail: float, extreme: float, weight: float = 0.0) -> None:
        self._orders.append(Order("trailing", side, size, trail=trail, extreme=extreme, weight=weight))

    def check(self, bar) -> list:
        """Return fired conditionals (removed from the book); ratchets trailing extremes in-place."""
        fired, still = [], []
        for o in self._orders:
            if order_fill_price(o, bar) is not None:   # None = no fire (extreme ratcheted in place)
                fired.append(o)
            else:
                still.append(o)
        self._orders = still
        return fired

    def clear(self) -> None:
        self._orders = []

    def __len__(self) -> int:
        return len(self._orders)
