"""Pure parse of a Binance USDⓈ-M futures /fapi/v1/exchangeInfo payload into the same
RiskLimits-shaped filters dict the spot path builds (parse_symbol_filters), plus base_asset.

Binance USDⓈ-M perpetuals differ from spot in TWO filter keys:
  - market order qty cap -> MARKET_LOT_SIZE.maxQty  (spot uses LOT_SIZE.maxQty)
  - min notional -> MIN_NOTIONAL.notional (spot uses MIN_NOTIONAL.minNotional)

Qty is in BASE asset (NOT contracts like OKX ct_val).
All other keys and the output dict shape are identical so RiskLimits and format_qty/format_price
consume this dict unchanged.
"""

from __future__ import annotations


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_binance_perp_instruments(payload: dict) -> dict[str, dict]:
    """Parse a Binance USDⓈ-M perpetual exchangeInfo payload into RiskLimits-shaped filter dicts."""
    out: dict[str, dict] = {}
    for entry in payload.get("symbols", []):
        symbol = str(entry.get("symbol", "")).upper()
        if not symbol:
            continue
        by_type = {flt.get("filterType"): flt for flt in entry.get("filters", [])}
        pf = by_type.get("PRICE_FILTER", {}) or {}
        lot = by_type.get("LOT_SIZE", {}) or {}
        mkt = by_type.get("MARKET_LOT_SIZE", {}) or {}
        notional = by_type.get("MIN_NOTIONAL", {}) or {}
        out[symbol] = {
            "tick_size": _f(pf, "tickSize"),
            "step_size": _f(lot, "stepSize"),
            "min_qty": _f(lot, "minQty"),
            "max_qty": _f(mkt, "maxQty"),          # MARKET_LOT_SIZE cap for market orders
            "min_notional": _f(notional, "notional"),
            "base_asset": str(entry.get("baseAsset", "")),
        }
    return out
