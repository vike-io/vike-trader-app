"""Shared row->Bar transform for the crypto REST sources (bybit / coinbase / kraken / okx).

Each exchange returns OHLCV rows in a different column ORDER and timestamp UNIT, but the parse is
otherwise identical (cast fields, build a Bar, sort ascending). ``rows_to_bars`` centralizes that
loop; each source supplies its own column-index map + ts scale, so the cast/sort logic lives in one
place and a new source can't get it subtly wrong.
"""

from ..core.model import Bar


def rows_to_bars(rows, cols: dict, ts_scale: int = 1) -> list[Bar]:
    """Build ts-ascending ``Bar``s from raw exchange rows.

    ``cols`` maps each Bar field to its row index (keys: ``ts, open, high, low, close, volume``);
    the timestamp is multiplied by ``ts_scale`` (1 for millisecond rows, 1000 for second rows).
    Rows may arrive in any order — the output is always sorted ascending by timestamp.
    """
    bars = [Bar(ts=int(r[cols["ts"]]) * ts_scale,
                open=float(r[cols["open"]]), high=float(r[cols["high"]]),
                low=float(r[cols["low"]]), close=float(r[cols["close"]]),
                volume=float(r[cols["volume"]])) for r in rows]
    bars.sort(key=lambda b: b.ts)
    return bars
