"""Dev utility: seed local Parquet with Binance 1m klines, then smoke-test the engine.

Run:  uv run python scripts/seed_binance.py
"""

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.data.binance_source import fetch_bars
from vike_trader_app.data.parquet_source import read_bars_parquet, write_bars_parquet

OUT = "storage/parquet/BTCUSDT/1m.parquet"
HOSTS = ("https://api.binance.com", "https://data-api.binance.vision")


class BuyAndHold(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(0.01)


def _fetch():
    last = None
    for base in HOSTS:
        try:
            bars = fetch_bars("BTCUSDT", "1m", 1000, base)
            print(f"fetched {len(bars)} bars from {base}")
            return bars
        except Exception as exc:  # noqa: BLE001 - report and try the next host
            print(f"  {base} failed: {exc}")
            last = exc
    raise SystemExit(f"all Binance hosts failed: {last}")


def main() -> None:
    bars = _fetch()
    write_bars_parquet(bars, OUT)
    print(f"seeded -> {OUT}")

    loaded = read_bars_parquet(OUT)
    res = BacktestEngine(loaded, BuyAndHold(), fee_rate=0.001).run()
    print(
        f"smoke: bars={len(loaded)} trades={len(res.trades)} "
        f"first_open={loaded[0].open} last_close={loaded[-1].close} "
        f"final_equity={res.final_equity:.2f}"
    )


if __name__ == "__main__":
    main()
