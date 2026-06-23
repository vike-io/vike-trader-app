"""TesterConfig.portfolio_engine_kwargs(): the config-derived PortfolioEngine args, one place."""

from vike_trader_app.core import sizing
from vike_trader_app.tester.config import TesterConfig


def test_portfolio_engine_kwargs_carries_all_config_derived_args():
    sz = sizing.FixedSharesSizer(5)
    cfg = TesterConfig(cash=5000.0, fee_rate=0.001, slippage=0.0005, multiplier=2.0,
                       leverage=3.0, maint_margin=0.05, cash_gate=True, sizer=sz,
                       max_open_long=4, max_open_short=2, volume_limit=0.1, timeframes=["5m"])
    kw = cfg.portfolio_engine_kwargs()
    assert kw["cash"] == 5000.0 and kw["fee_rate"] == 0.001 and kw["slippage"] == 0.0005
    assert kw["multiplier"] == 2.0 and kw["leverage"] == 3.0 and kw["maint_margin"] == 0.05
    assert kw["cash_gate"] is True and kw["sizer"] is sz
    assert kw["max_open_long"] == 4 and kw["max_open_short"] == 2
    assert kw["volume_limit"] == 0.1 and kw["timeframes"] == ["5m"]
    assert kw["maker_fee"] is None and kw["taker_fee"] is None


def test_defaults_match_engine_defaults():
    kw = TesterConfig().portfolio_engine_kwargs()
    assert kw["sizer"] is None and kw["max_open_long"] == 0 and kw["volume_limit"] is None
    assert kw["cash_gate"] is False
