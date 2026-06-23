"""Pure parse of a Bybit V5 /v5/market/instruments-info (category=spot) payload into the same
RiskLimits-shaped filters dict the Binance path builds (parse_symbol_filters), plus base_asset.

Bybit gives basePrecision (the qty STEP) directly and tickSize like Binance; minOrderAmt is the
MIN_NOTIONAL analog. Same dict keys so RiskLimits and format_qty/format_price consume it unchanged.
"""

from __future__ import annotations


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_bybit_instruments_info(payload: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in payload.get("result", {}).get("list", []):
        symbol = str(entry.get("symbol", "")).upper()
        if not symbol:
            continue
        price_filter = entry.get("priceFilter", {}) or {}
        lot = entry.get("lotSizeFilter", {}) or {}
        out[symbol] = {
            "tick_size": _f(price_filter, "tickSize"),
            "step_size": _f(lot, "basePrecision"),
            "min_qty": _f(lot, "minOrderQty"),
            "max_qty": _f(lot, "maxOrderQty"),
            "min_notional": _f(lot, "minOrderAmt"),
            "base_asset": str(entry.get("baseCoin", "")),
        }
    return out
