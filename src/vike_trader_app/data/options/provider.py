"""OptionsProvider protocol + underlying -> provider routing."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .deribit import DeribitOptionsProvider
from .model import Expiry, OptionChain
from .yfinance import YFinanceOptionsProvider

CRYPTO_UNDERLYINGS = {"BTC", "ETH", "SOL"}


@runtime_checkable
class OptionsProvider(Protocol):
    name: str
    asset_class: str

    def list_underlyings(self) -> list[str]: ...
    def list_expiries(self, underlying: str) -> list[Expiry]: ...
    def fetch_chain(self, underlying: str, expiry: Expiry, strikes: int | None = None) -> OptionChain: ...


def select_provider(underlying: str) -> OptionsProvider:
    """Crypto tickers -> Deribit; everything else (equities/indices) -> yfinance."""
    if underlying.upper().lstrip("^") in CRYPTO_UNDERLYINGS:
        return DeribitOptionsProvider()
    return YFinanceOptionsProvider()
