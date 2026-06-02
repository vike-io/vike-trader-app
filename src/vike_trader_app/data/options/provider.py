"""OptionsProvider protocol + underlying -> provider routing."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from .deribit import DeribitOptionsProvider
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
    """Equity/index backend: Polygon when explicitly opted in, else the free yfinance feed.

    Polygon's free tier 403s on the options snapshot (no bid/ask/IV/greeks), so it is opt-in
    via `options_stock_provider=polygon` (with `polygon_api_key` set) rather than auto-selected
    by key presence — that keeps the working default on yfinance for free users.
    """
    if (os.environ.get("options_stock_provider", "").lower() == "polygon"
            and os.environ.get("polygon_api_key")):
        return PolygonOptionsProvider()
    return YFinanceOptionsProvider()


def select_provider(underlying: str) -> OptionsProvider:
    """Crypto tickers -> Deribit; everything else (equities/indices) -> the stock backend."""
    if underlying.upper().lstrip("^") in CRYPTO_UNDERLYINGS:
        return DeribitOptionsProvider()
    return _stock_provider()
