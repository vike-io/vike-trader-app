"""Derive a coarse topic category from a headline (+ feed tags).

Our free RSS feeds carry no licensed taxonomy (unlike TradingView's Economics/Sector/Corporate-
activity filters), so this is a deliberately simple, transparent keyword classifier. First rule
that matches wins; everything else is "Markets". Used by the Category filter and the reader chip.
"""
from __future__ import annotations

import re

from .models import NewsItem

# Display order for the Category dropdown (UI lists these; "Markets" is the catch-all).
CATEGORIES: list[str] = [
    "Earnings", "M&A", "Macro", "Crypto", "Regulation", "Commodities", "Tech", "Markets",
]

# (category, keywords) — ordered; first match wins. Keywords are matched at a WORD BOUNDARY
# start (\b), so a stem like "commodit" still catches "commodity/commodities" but "merge" no
# longer fires on "emergency", "ban" on "urban", "profit" on "nonprofit", "eps" on "steps".
# Avoid bare ultra-common words (e.g. "results", "revenue") that aren't earnings-specific.
_RULES_RAW: list[tuple[str, tuple[str, ...]]] = [
    # trailing-space keywords (e.g. "eps ", "sec ") only match the standalone token, never a
    # longer word ("epstein", "sector"); the rest match at a word start (catching inflections).
    ("Earnings", ("earnings", "quarterly results", "earnings results", "eps ", "guidance",
                  "profit", "beats estimates", "misses estimates", "tops estimates",
                  "operating margin", "net income")),
    ("M&A", ("acquire", "acquisition", "merger", "merge", "takeover", "buyout", "to buy",
             "stake in", "deal to", "in talks to")),
    ("Regulation", ("sec ", "regulator", "lawsuit", "antitrust", "probe", "subpoena",
                    "sanction", "settlement", "indict", "banned", "to ban", "ban on")),
    ("Macro", ("inflation", "cpi", "ppi", "gdp", "jobless", "payroll", "unemployment",
               "rate decision", "rate cut", "rate hike", "interest rate", "fed ", "fomc",
               "ecb ", "boe ", "central bank", "yields", "treasury", "recession", "emergency")),
    ("Commodities", ("oil", "crude", "brent", "wti", "gold", "silver", "copper", "natural gas",
                     "opec", "commodit")),
    ("Crypto", ("bitcoin", "btc", "ethereum", "eth ", "crypto", "token", "blockchain", "defi ",
                "stablecoin", "altcoin", "solana", "xrp", "binance", "etf inflow")),
    ("Tech", ("artificial intelligence", "ai ", "chip", "semiconductor", "nvidia", "software",
              "cloud", "data center", "iphone", "app store", "openai")),
]

# Precompile one regex per category: a word-boundary-anchored alternation of its keywords.
_RULES: list[tuple[str, "re.Pattern[str]"]] = [
    (cat, re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")"))
    for cat, kws in _RULES_RAW
]


def classify(item: NewsItem) -> str:
    """Best-effort topic bucket for a headline. Cheap; safe to call per-item per-render."""
    hay = f" {(item.title or '').lower()} {' '.join(item.tags).lower()} "
    for category, pattern in _RULES:
        if pattern.search(hay):
            return category
    return "Markets"
