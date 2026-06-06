"""Forward (paper) testing: engine.step() reuse, PollingBarFeed, PaperTester."""

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.paper import PaperTester, pump
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.data.store import Store


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _bars():
    return [
        _bar(0, 100, 100),
        _bar(60_000, 110, 110),
        _bar(120_000, 120, 120),
        _bar(180_000, 130, 130),
    ]


class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()


# --- engine.step() must reproduce run() when fed one bar at a time -------------

def test_stepping_bars_one_by_one_matches_run():
    bars = _bars()
    full = BacktestEngine(bars, _BuyThenClose(), cash=10_000.0).run()

    eng = BacktestEngine(bars, _BuyThenClose(), cash=10_000.0)
    eq = [eng.step(bar, i) for i, bar in enumerate(bars)]

    assert eq == full.equity_curve
    assert [t.pnl for t in eng.trades] == [t.pnl for t in full.trades]
    assert eng.equity_now() == full.final_equity


# --- PaperTester: same fills as the backtest, driven bar-by-bar -------------

def test_live_bars_fill_at_next_open_exactly_like_backtest():
    ft = PaperTester(symbol="BTCUSDT", interval="1m", strategy=_BuyThenClose(), cash=10_000.0)
    for bar in _bars():
        ft.on_bar_live(bar)
    res = ft.result()
    assert len(res.trades) == 1
    assert res.trades[0].entry_price == 110.0  # next-open after the buy — no look-ahead
    assert res.trades[0].exit_price == 130.0
    assert res.final_equity == 10_020.0
    assert res.equity_curve == BacktestEngine(_bars(), _BuyThenClose(), cash=10_000.0).run().equity_curve


class _CountCalls(Strategy):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def on_bar(self, bar):
        self.calls += 1


def test_seed_bars_warm_up_strategy_but_stay_out_of_the_live_curve():
    strat = _CountCalls()
    ft = PaperTester(
        symbol="X", interval="1m", strategy=strat, cash=10_000.0, seed_bars=_bars()[:2]
    )
    assert strat.calls == 2  # warmed up on the 2 seed bars at construction
    for bar in _bars()[2:]:
        ft.on_bar_live(bar)
    assert strat.calls == 4  # 2 seed + 2 live
    assert len(ft.equity_curve) == 2  # live curve only the 2 live bars


def test_each_live_bar_is_persisted_to_the_store():
    s = Store(":memory:")
    ft = PaperTester(symbol="BTCUSDT", interval="1m", strategy=_BuyThenClose(), cash=10_000.0, store=s)
    for bar in _bars():
        ft.on_bar_live(bar)
    assert [b.ts for b in s.forward_bars(ft.run_id)] == [0, 60_000, 120_000, 180_000]
    assert s.list_forward_runs()[0].strategy == "_BuyThenClose"


def test_resume_reconstructs_state_from_the_store():
    s = Store(":memory:")
    ft = PaperTester(symbol="BTCUSDT", interval="1m", strategy=_BuyThenClose(), cash=10_000.0, store=s)
    for bar in _bars():
        ft.on_bar_live(bar)
    final = ft.result().final_equity

    resumed = PaperTester.resume(s, ft.run_id, strategy=_BuyThenClose())
    assert resumed.result().final_equity == final
    assert [t.pnl for t in resumed.result().trades] == [20.0]
    # resume replays without duplicating stored bars
    assert len(s.forward_bars(ft.run_id)) == 4


def test_pump_drives_each_polled_bar_into_the_tester():
    class _FakeFeed:
        def __init__(self, batches):
            self._batches = list(batches)
        def poll_once(self):
            return self._batches.pop(0) if self._batches else []

    feed = _FakeFeed([_bars()[:2], _bars()[2:]])
    ft = PaperTester(symbol="X", interval="1m", strategy=_BuyThenClose(), cash=10_000.0)
    first = pump(feed, ft)
    second = pump(feed, ft)
    assert [b.ts for b in first] == [0, 60_000]
    assert [b.ts for b in second] == [120_000, 180_000]
    assert len(ft.equity_curve) == 4  # all four flowed through on_bar_live


class _RecordHigherTF(Strategy):
    """Records, at each bar, the closes of the completed 2m bars it can see (live)."""

    def __init__(self):
        super().__init__()
        self.seen = []

    def on_bar(self, bar):
        self.seen.append((self.index, [b.close for b in self.bars("2m")]))


def test_forward_exposes_completed_higher_tf_bars_live_without_lookahead():
    strat = _RecordHigherTF()
    ft = PaperTester(symbol="X", interval="1m", strategy=strat, timeframes=["2m"])
    for bar in _bars():  # closes 100,110,120,130 at ts 0,60k,120k,180k
        ft.on_bar_live(bar)
    # Identical visibility to the backtest engine (deliver-on-complete, look-ahead-safe):
    assert strat.seen == [(0, []), (1, []), (2, [110.0]), (3, [110.0])]


def test_on_step_callback_fires_per_live_bar_with_equity():
    seen = []
    ft = PaperTester(
        symbol="X", interval="1m", strategy=_BuyThenClose(), cash=10_000.0,
        on_step=lambda bar, eq: seen.append((bar.ts, eq)),
    )
    for bar in _bars():
        ft.on_bar_live(bar)
    assert [ts for ts, _ in seen] == [0, 60_000, 120_000, 180_000]
    assert seen[-1][1] == 10_020.0


def test_paper_tester_new_name_and_backcompat_shim():
    from vike_trader_app.core.paper import PaperTester
    from vike_trader_app.core import forward as _shim
    assert _shim.ForwardTester is PaperTester  # old import path still resolves
