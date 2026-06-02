"""OptionsProvider protocol + underlying -> provider routing."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from .deribit import DeribitOptionsProvider
from .marketdata import MarketDataOptionsProvider
from .model import Expiry, OptionChain
from .polygon import PolygonOptionsProvider
from .yfinance import YFinanceOptionsProvider

CRYPTO_UNDERLYINGS = {"BTC", "ETH", "SOL"}


@runtime_checkable
class OptionsProvider(Protocol):
    name: str
    asset_class: str

    def list_underlyings(self) -> list[str]: ...
    def list_expiries(self, underlying: str) -> list[Expiry]: ...
    def fetch_chain(self, underlying: str, expiry: Expiry, strikes: int | None = None) -> OptionChain: ...


def _stock_provider() -> OptionsProvider:
    """Equity/index backend, chosen by `options_stock_provider` (+ that backend's key):
    'marketdata' (free delayed greeks), 'polygon' (paid Options entitlement), else the free
    yfinance feed. Opt-in by flag rather than key-presence so the default stays on yfinance.
    """
    backend = os.environ.get("options_stock_provider", "").lower()
    if backend == "marketdata" and os.environ.get("marketdata_api_key"):
        return MarketDataOptionsProvider()
    if backend == "polygon" and os.environ.get("polygon_api_key"):
        return PolygonOptionsProvider()
    return YFinanceOptionsProvider()


def select_provider(underlying: str) -> OptionsProvider:
    """Crypto tickers -> Deribit; everything else (equities/indices) -> the stock backend."""
    if underlying.upper().lstrip("^") in CRYPTO_UNDERLYINGS:
        return DeribitOptionsProvider()
    return _stock_provider()
