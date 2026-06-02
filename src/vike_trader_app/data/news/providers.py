"""Tier-0 free news providers + symbol normalization. No keys, no licensed sources.

``broad`` feeds have a fixed URL; ``symbol`` feeds template {SYMBOL}/{QUERY} against the
active chart symbol. Each provider's feed path is verified live at fetch time — a moved/dead
feed simply contributes nothing (see fetch.py), it never breaks the others.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    market: str          # crypto|forex|stocks|global
    url: str             # may contain {SYMBOL} or {QUERY}
    kind: str            # "broad" | "symbol"
    enabled: bool = True


PROVIDERS: list[ProviderSpec] = [
    # crypto (broad)
    ProviderSpec("CoinDesk", "crypto", "https://www.coindesk.com/arc/outboundfeeds/rss/", "broad"),
    ProviderSpec("Cointelegraph", "crypto", "https://cointelegraph.com/rss", "broad"),
    ProviderSpec("Decrypt", "crypto", "https://decrypt.co/feed", "broad"),
    ProviderSpec("CryptoSlate", "crypto", "https://cryptoslate.com/feed/", "broad"),
    ProviderSpec("BeInCrypto", "crypto", "https://beincrypto.com/feed/", "broad"),
    ProviderSpec("Bitcoin Magazine", "crypto", "https://bitcoinmagazine.com/feed", "broad"),
    ProviderSpec("NewsBTC", "crypto", "https://www.newsbtc.com/feed/", "broad"),
    ProviderSpec("CoinJournal", "crypto", "https://coinjournal.net/feed/", "broad"),
    # forex / macro (broad)
    ProviderSpec("FXStreet", "forex", "https://www.fxstreet.com/rss/news", "broad"),
    ProviderSpec("ForexLive", "forex", "https://www.forexlive.com/feed/news/", "broad"),
    ProviderSpec("DailyFX", "forex", "https://www.dailyfx.com/feeds/market-news", "broad"),
    ProviderSpec("Investing.com", "forex", "https://www.investing.com/rss/news.rss", "broad"),
    # equities / general (broad)
    ProviderSpec("MarketWatch", "stocks", "http://feeds.marketwatch.com/marketwatch/topstories/", "broad"),
    ProviderSpec("CNBC", "stocks", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "broad"),
    ProviderSpec("Seeking Alpha", "stocks", "https://seekingalpha.com/market_currents.xml", "broad"),
    # symbol-linked (templated)
    ProviderSpec("NASDAQ", "stocks", "https://www.nasdaq.com/feed/rssoutbound?symbol={SYMBOL}", "symbol"),
    ProviderSpec("Yahoo Finance", "stocks",
                 "https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}&region=US&lang=en-US", "symbol"),
    ProviderSpec("Google News", "global", "https://news.google.com/rss/search?q={QUERY}", "symbol"),
]

# Common majors → (base asset, full name). Passthrough fallback for everything else.
_CRYPTO_BASE = {
    "BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "XRPUSDT": "XRP",
    "BNBUSDT": "BNB", "ADAUSDT": "ADA", "DOGEUSDT": "DOGE", "AVAXUSDT": "AVAX",
    "LINKUSDT": "LINK", "MATICUSDT": "MATIC", "DOTUSDT": "DOT", "LTCUSDT": "LTC",
}
_CRYPTO_NAME = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP", "BNB": "BNB",
    "ADA": "Cardano", "DOGE": "Dogecoin", "AVAX": "Avalanche", "LINK": "Chainlink",
    "MATIC": "Polygon", "DOT": "Polkadot", "LTC": "Litecoin",
}


@dataclass(frozen=True)
class NormalizedSymbol:
    ticker: str          # for {SYMBOL}
    query: str           # for {QUERY}
    base: str            # base asset (BTC, AAPL, EUR)


def normalize(symbol: str) -> NormalizedSymbol:
    """Map the app's exchange-style symbol to each provider's expected form."""
    s = (symbol or "").upper().strip()
    if s in _CRYPTO_BASE:                       # crypto pair, e.g. BTCUSDT
        base = _CRYPTO_BASE[s]
        name = _CRYPTO_NAME.get(base, base)
        return NormalizedSymbol(ticker=f"{base}-USD", query=f'"{name}" OR {base}', base=base)
    if len(s) == 6 and s.isalpha():             # forex pair, e.g. EURUSD
        return NormalizedSymbol(ticker=s, query=f"{s[:3]}/{s[3:]}", base=s[:3])
    return NormalizedSymbol(ticker=s, query=s, base=s)   # equities / passthrough


def build_url(spec: ProviderSpec, symbol: str | None) -> str | None:
    """Resolve a provider's fetch URL for ``symbol`` (None for symbol feeds → skip)."""
    if spec.kind == "broad":
        return spec.url
    if not symbol:
        return None
    n = normalize(symbol)
    return spec.url.replace("{SYMBOL}", n.ticker).replace("{QUERY}", quote_plus(n.query))
