"""Derive a coarse topic category from a headline (+ feed tags).

Our free RSS feeds carry no licensed taxonomy (unlike TradingView's Economics/Sector/Corporate-
activity filters), so this is a deliberately simple, transparent keyword classifier. First rule
that matches wins; everything else is "Markets". Used by the Category filter and the reader chip.
"""
from __future__ import annotations

from .models import NewsItem

# Display order for the Category dropdown (UI lists these; "Markets" is the catch-all).
CATEGORIES: list[str] = [
    "Earnings", "M&A", "Macro", "Crypto", "Regulation", "Commodities", "Tech", "Markets",
]

# (category, keywords) — ordered; first containing-match wins. Keep lowercase.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Earnings", ("earnings", "quarterly results", "q1 ", "q2 ", "q3 ", "q4 ", "eps ",
                  "revenue", "guidance", "profit", "beats estimates", "misses estimates",
                  "tops estimates", "results")),
    ("M&A", ("acquire", "acquisition", "merger", "merge", "takeover", "buyout", "to buy ",
             "stake in", "deal to", "in talks to")),
    ("Regulation", ("sec ", "regulator", "lawsuit", "court", "fine", "probe", "investigation",
                    "ban ", "sanction", "antitrust", "subpoena", "settlement", "indict")),
    ("Macro", ("inflation", "cpi", "ppi", "gdp", "jobless", "payroll", "unemployment",
               "rate decision", "rate cut", "rate hike", "interest rate", "fed ", "fomc",
               "ecb ", "boe ", "central bank", "yields", "treasury", "recession")),
    ("Commodities", ("oil", "crude", "brent", "wti", "gold", "silver", "copper", "natural gas",
                     "opec", "commodit")),
    ("Crypto", ("bitcoin", "btc", "ethereum", "eth ", "crypto", "token", "blockchain", "defi",
                "stablecoin", "altcoin", "solana", "xrp", "binance", "etf inflow")),
    ("Tech", ("ai ", "artificial intelligence", "chip", "semiconductor", "nvidia", "software",
              "cloud", "data center", "iphone", "app store", "openai", "model")),
]


def classify(item: NewsItem) -> str:
    """Best-effort topic bucket for a headline. Cheap; safe to call per-item per-render."""
    hay = f" {(item.title or '').lower()} {' '.join(item.tags).lower()} "
    for category, keywords in _RULES:
        if any(kw in hay for kw in keywords):
            return category
    return "Markets"
