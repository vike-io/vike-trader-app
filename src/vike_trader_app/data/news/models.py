"""Core news data types — a parsed headline and a feed filter. Pure data, no I/O."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def make_id(url: str, title: str, source: str) -> str:
    """Stable 16-char dedupe key: the url when present, else title+source."""
    key = (url or "").strip() or f"{(title or '').strip()}|{(source or '').strip()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class NewsItem:
    """One headline. ``summary`` is a plain-text snippet (HTML stripped) — never full body."""

    id: str
    title: str
    url: str
    summary: str
    source: str          # provider display name, e.g. "CoinDesk"
    market: str          # "crypto" | "forex" | "stocks" | "global"
    published_ms: int    # epoch milliseconds, UTC
    symbols: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()    # feed-supplied <category> labels (TV-style topic tags)


@dataclass(frozen=True)
class NewsFilter:
    """Active feed filter. Empty/None fields mean 'no constraint on this dimension'."""

    market: str | None = None        # single-market (legacy / saved feeds)
    markets: frozenset[str] = frozenset()   # multi-market select (takes precedence over `market`)
    providers: frozenset[str] = frozenset()
    symbol: str | None = None        # set when "Follow chart" is on
    query: str = ""
    categories: frozenset[str] = frozenset()   # derived topic categories (see news.classify)
