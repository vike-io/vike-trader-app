from vike_trader_app.data.options.deribit import DeribitOptionsProvider
from vike_trader_app.data.options.marketdata import MarketDataOptionsProvider
from vike_trader_app.data.options.polygon import PolygonOptionsProvider
from vike_trader_app.data.options.provider import select_provider
from vike_trader_app.data.options.yfinance import YFinanceOptionsProvider


def test_crypto_underlyings_route_to_deribit():
    assert isinstance(select_provider("BTC"), DeribitOptionsProvider)
    assert isinstance(select_provider("eth"), DeribitOptionsProvider)


def test_stocks_default_to_yfinance(monkeypatch):
    # no opt-in flag -> free yfinance backend (the working default)
    monkeypatch.delenv("options_stock_provider", raising=False)
    assert isinstance(select_provider("^VIX"), YFinanceOptionsProvider)
    assert isinstance(select_provider("AAPL"), YFinanceOptionsProvider)
    assert isinstance(select_provider("SPY"), YFinanceOptionsProvider)


def test_stocks_route_to_polygon_when_opted_in(monkeypatch):
    monkeypatch.setenv("options_stock_provider", "polygon")
    monkeypatch.setenv("polygon_api_key", "test-key")
    assert isinstance(select_provider("MSFT"), PolygonOptionsProvider)
    # crypto still goes to Deribit regardless of the stock-backend flag
    assert isinstance(select_provider("BTC"), DeribitOptionsProvider)


def test_stocks_route_to_marketdata_when_opted_in(monkeypatch):
    monkeypatch.setenv("options_stock_provider", "marketdata")
    monkeypatch.setenv("marketdata_api_key", "test-key")
    assert isinstance(select_provider("AAPL"), MarketDataOptionsProvider)
    assert isinstance(select_provider("BTC"), DeribitOptionsProvider)


def test_polygon_flag_without_key_falls_back_to_yfinance(monkeypatch):
    monkeypatch.setenv("options_stock_provider", "polygon")
    monkeypatch.delenv("polygon_api_key", raising=False)
    assert isinstance(select_provider("MSFT"), YFinanceOptionsProvider)
