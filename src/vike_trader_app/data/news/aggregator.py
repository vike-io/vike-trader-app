"""Merge incoming items into the running feed (dedupe + sort) and apply the active filter."""

from __future__ import annotations

from .models import NewsFilter, NewsItem
from .providers import NormalizedSymbol, normalize


def merge(existing: list[NewsItem], incoming: list[NewsItem], *, cap: int = 500) -> list[NewsItem]:
    """Union by id (incoming wins), newest first, capped to ``cap`` items."""
    by_id: dict[str, NewsItem] = {it.id: it for it in existing}
    by_id.update((it.id, it) for it in incoming)
    return sorted(by_id.values(), key=lambda it: it.published_ms, reverse=True)[:cap]


def _matches_symbol(item: NewsItem, n: NormalizedSymbol) -> bool:
    if n.base in item.symbols or n.ticker in item.symbols:
        return True
    hay = f"{item.title} {item.summary}".lower()
    name = _CRYPTO_NAME_LOWER.get(n.base)
    needles = [n.base.lower(), name] if name else [n.base.lower()]
    return any(x and x in hay for x in needles)


def apply_filter(items: list[NewsItem], flt: NewsFilter) -> list[NewsItem]:
    out = items
    if flt.market:
        out = [it for it in out if it.market == flt.market]
    if flt.providers:
        out = [it for it in out if it.source in flt.providers]
    if flt.symbol:
        n = normalize(flt.symbol)
        out = [it for it in out if _matches_symbol(it, n)]
    if flt.query:
        q = flt.query.lower()
        out = [it for it in out if q in it.title.lower() or q in it.summary.lower()]
    return out


# lowercase base→name for text matching (e.g. "BTC" → "bitcoin")
from .providers import _CRYPTO_NAME  # noqa: E402

_CRYPTO_NAME_LOWER = {b: nm.lower() for b, nm in _CRYPTO_NAME.items()}
