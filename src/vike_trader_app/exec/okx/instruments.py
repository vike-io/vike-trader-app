"""Pure parse of an OKX V5 /api/v5/public/instruments (instType=SPOT) payload into the same
RiskLimits-shaped filters dict the Binance path builds (parse_symbol_filters), plus base_asset.

OKX gives lotSz (qty step), tickSz (price step), minSz (min order qty), and maxMktSz (max mkt qty).
There is no explicit min_notional for OKX SPOT so it defaults to 0.0.
Same dict keys so RiskLimits and format_qty/format_price consume it unchanged.
"""

from __future__ import annotations


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_okx_instruments(payload: dict) -> dict[str, dict]:
    """Return a mapping of OKX instId -> filters dict (same shape as Bybit/Binance parsers)."""
    out: dict[str, dict] = {}
    for entry in payload.get("data", []):
        inst_id = str(entry.get("instId", "")).upper()
        if not inst_id:
            continue
        out[inst_id] = {
            "tick_size": _f(entry, "tickSz"),
            "step_size": _f(entry, "lotSz"),
            "min_qty": _f(entry, "minSz"),
            "max_qty": _f(entry, "maxMktSz"),
            "min_notional": 0.0,
            "base_asset": str(entry.get("baseCcy", "")),
        }
    return out
