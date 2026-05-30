"""Prove deep history + caching against live Binance.

Pulls N days of bars (paginated, far past Binance's 1000-per-call cap), caches to
Parquet, then re-loads from cache to show the speedup.

Run:  uv run python scripts/fetch_history.py [SYMBOL] [INTERVAL] [DAYS]
e.g.  uv run python scripts/fetch_history.py BTCUSDT 1m 30
"""

import sys
import time

from vike_trader_app.data.binance_source import fetch_bars_range
from vike_trader_app.data.cache import cache_path, get_bars

DAY_MS = 86_400_000


def _progress(done, start, end):
    pct = (done - start) / max(end - start, 1) * 100
    print(f"\r  fetching… {pct:5.1f}%", end="", flush=True)


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    interval = sys.argv[2] if len(sys.argv) > 2 else "1m"
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 30

    now = int(time.time() * 1000)
    start = now - days * DAY_MS

    def fetcher(sym, iv, s, e, progress=None):
        return fetch_bars_range(sym, iv, s, e, pause=0.2, progress=progress)

    print(f"=== {symbol} {interval} · last {days} days ===")
    t0 = time.time()
    bars = get_bars(symbol, interval, start, now, fetcher=fetcher, progress=_progress)
    t1 = time.time()
    print()
    print(f"first fetch : {len(bars):,} bars in {t1 - t0:.1f}s  (vs the old 1000-bar ceiling)")
    print(f"cached to   : {cache_path('storage/parquet', symbol, interval)}")

    t2 = time.time()
    bars2 = get_bars(symbol, interval, start, now, fetcher=fetcher)
    t3 = time.time()
    print(f"cache reload: {len(bars2):,} bars in {t3 - t2:.2f}s  (no network)")
    if t3 - t2 > 0:
        print(f"speedup     : ~{(t1 - t0) / max(t3 - t2, 0.01):.0f}x")

    if bars:
        span_h = (bars[-1].ts - bars[0].ts) / 3_600_000
        print(f"coverage    : {span_h:,.0f} hours of {interval} bars")


if __name__ == "__main__":
    main()
