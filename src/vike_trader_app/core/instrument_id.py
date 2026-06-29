"""Instrument identity: "SYMBOL.VENUE" (bare symbol = the run's default venue).

Mirrors Nautilus's InstrumentId so cross-venue HFT can distinguish BTCUSDT on
different venues (BTCUSDT.BINANCE vs BTCUSDT.BYBIT)."""


def parse_instrument(s: str, default_venue: str | None = None) -> tuple[str | None, str]:
    """Return (venue, symbol). "BTCUSDT.BYBIT" -> ("bybit","BTCUSDT"); bare uses default_venue."""
    if "." in s:
        symbol, venue = s.rsplit(".", 1)
        return venue.lower(), symbol
    return (default_venue.lower() if default_venue else None), s


def format_instrument(venue: str | None, symbol: str) -> str:
    """Inverse of parse_instrument. No venue -> bare symbol."""
    return f"{symbol}.{venue.upper()}" if venue else symbol
