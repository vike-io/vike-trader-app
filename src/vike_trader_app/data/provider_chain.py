"""Ordered provider fallback chain (Wealth-Lab's 'Historical Providers' list).

Tries each provider's history fetcher in the order given and returns the first that yields data —
generalising the forex Yahoo→Dukascopy stitch into a user-orderable chain. A provider that errors
is skipped (the next is tried), so one dead endpoint never blocks the rest.
"""

import logging

from .sources import select_source

log = logging.getLogger(__name__)


def fetch_chain(provider_names, symbol, interval, start_ms, end_ms, progress=None,
                select=select_source, settings_by_provider=None, mappings=None):
    """Return ``(bars, provider_used)`` from the first provider in ``provider_names`` with data.

    ``select(symbol, provider=name, settings=...)`` resolves each provider's ``Source``
    (injectable for tests). ``settings_by_provider`` is an optional ``{provider_name: dict}``
    map of persisted per-provider settings; each entry is forwarded to ``select`` so the fetcher
    can be pre-bound with base_url/pause/api_key.

    ``mappings`` is an optional ``SymbolMappings`` instance. When provided, the symbol is
    rewritten per-provider at fetch time (the caller's cache still keys on the original symbol).
    Returns ``([], None)`` if every provider is empty or errors.
    """
    from .symbol_mappings import apply_mapping

    names = list(provider_names)
    causes: list[str] = []   # per-provider reason, surfaced once if the whole chain comes up empty
    for name in names:
        try:
            fetch_symbol = apply_mapping(symbol, name, mappings) if mappings else symbol
            settings = (settings_by_provider or {}).get(name)
            src = select(fetch_symbol, provider=name, settings=settings)
            bars = src.fetch_bars_range(fetch_symbol, interval, start_ms, end_ms, progress=progress)
        except Exception as e:  # noqa: BLE001 - a failing provider is skipped; try the next
            log.debug("provider %s failed for %s %s: %s", name, symbol, interval, e)
            causes.append(f"{name}: {type(e).__name__}: {e}")
            continue
        if bars:
            return bars, name
        log.debug("provider %s returned no data for %s %s", name, symbol, interval)
        causes.append(f"{name}: empty")
    if names:   # the chain was non-empty but nothing yielded data — make the cause diagnosable
        log.warning("all %d providers failed/empty for %s %s: %s",
                    len(names), symbol, interval, "; ".join(causes))
    return [], None


def resolve_order(symbol, linked_provider, cfg):
    """Provider names to try, linked provider first, then the enabled chain in order.

    ``symbol`` is accepted for parity with ``fetch_for`` and reserved for future per-symbol
    routing; the current ordering is symbol-independent. A linked provider is always promoted
    to the front even when it's disabled in the config (an explicit per-DataSet override).
    """
    order = cfg.enabled_in_order()
    if linked_provider:
        order = [linked_provider] + [n for n in order if n != linked_provider]
    return order


def fetch_for(symbol, interval, start_ms, end_ms, *, root, linked_provider=None,
              progress=None, select=select_source):
    """Load ``symbol`` via the persisted provider chain. Returns ``(bars, provider_used)``.

    Reads per-provider settings and symbol mappings from the persisted config and forwards
    them to ``fetch_chain`` so that base_url/pause/api_key overrides and symbol rewrites are
    applied transparently at fetch time. The caller's cache still keys on the original symbol —
    only the per-provider fetch call uses the mapped symbol.
    """
    from .providers_config import load_providers_config
    from .symbol_mappings import load_mappings

    cfg = load_providers_config(root)
    order = resolve_order(symbol, linked_provider, cfg)
    settings_by_provider = {p.name: p.settings for p in cfg.providers if p.settings}
    mappings = load_mappings(root)
    return fetch_chain(order, symbol, interval, start_ms, end_ms, progress=progress,
                       select=select, settings_by_provider=settings_by_provider,
                       mappings=mappings)
