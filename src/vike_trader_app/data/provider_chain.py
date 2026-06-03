"""Ordered provider fallback chain (Wealth-Lab's 'Historical Providers' list).

Tries each provider's history fetcher in the order given and returns the first that yields data —
generalising the forex Yahoo→Dukascopy stitch into a user-orderable chain. A provider that errors
is skipped (the next is tried), so one dead endpoint never blocks the rest.
"""

from .sources import select_source


def fetch_chain(provider_names, symbol, interval, start_ms, end_ms, progress=None,
                select=select_source):
    """Return ``(bars, provider_used)`` from the first provider in ``provider_names`` with data.

    ``select(symbol, provider=name)`` resolves each provider's ``Source`` (injectable for tests).
    Returns ``([], None)`` if every provider is empty or errors.
    """
    for name in provider_names:
        try:
            src = select(symbol, provider=name)
            bars = src.fetch_bars_range(symbol, interval, start_ms, end_ms, progress=progress)
        except Exception:  # noqa: BLE001 - a failing provider is skipped; try the next
            continue
        if bars:
            return bars, name
    return [], None


def resolve_order(symbol, linked_provider, cfg):
    """Provider names to try for ``symbol``: linked provider first, then the enabled chain in order."""
    order = cfg.enabled_in_order()
    if linked_provider:
        order = [linked_provider] + [n for n in order if n != linked_provider]
    return order


def fetch_for(symbol, interval, start_ms, end_ms, *, root, linked_provider=None,
              progress=None, select=select_source):
    """Load ``symbol`` via the persisted provider chain. Returns ``(bars, provider_used)``."""
    from .providers_config import load_providers_config

    order = resolve_order(symbol, linked_provider, load_providers_config(root))
    return fetch_chain(order, symbol, interval, start_ms, end_ms, progress=progress, select=select)
