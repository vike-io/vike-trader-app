from vike_trader_app.core.model import Bar
from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.core.strategy_engine import StrategyEngine


def _bars(n=3):
    return [Bar(ts=t * 60_000, open=100, high=100, low=100, close=100) for t in range(n)]


def test_now_reflects_current_bar_ts():
    seen = []

    class S(Strategy):
        def on_bar(self, bar):
            seen.append((self.now, bar.ts))

    BacktestEngine(_bars(), S()).run()
    assert seen and all(now == ts for now, ts in seen)   # self.now == the bar being processed


def test_catalog_default_and_injected(tmp_path):
    e = BacktestEngine(_bars(), Strategy())
    assert e.catalog is not None                          # lazy default Catalog()
    sentinel = object()
    e2 = BacktestEngine(_bars(), Strategy(), catalog=sentinel)
    assert e2.catalog is sentinel                          # injected wins


def test_engine_still_satisfies_protocol():
    e = BacktestEngine(_bars(), Strategy())
    assert isinstance(e, StrategyEngine)                  # now is in the protocol; engine conforms
