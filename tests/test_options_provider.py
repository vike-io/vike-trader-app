from vike_trader_app.data.options.deribit import DeribitOptionsProvider
from vike_trader_app.data.options.provider import select_provider
from vike_trader_app.data.options.yfinance import YFinanceOptionsProvider


def test_crypto_underlyings_route_to_deribit():
    assert isinstance(select_provider("BTC"), DeribitOptionsProvider)
    assert isinstance(select_provider("eth"), DeribitOptionsProvider)


def test_everything_else_routes_to_yfinance():
    assert isinstance(select_provider("^VIX"), YFinanceOptionsProvider)
    assert isinstance(select_provider("AAPL"), YFinanceOptionsProvider)
    assert isinstance(select_provider("SPY"), YFinanceOptionsProvider)
