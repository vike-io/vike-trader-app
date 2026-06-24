"""Pure parse of a Bybit V5 /v5/market/instruments-info (category=linear) payload into the same
RiskLimits-shaped filters dict the spot path builds (parse_bybit_instruments_info), plus base_asset.

Linear perps differ from spot in TWO filter keys:
  - qty STEP  -> lotSizeFilter.qtyStep  (spot uses basePrecision)
  - min notional -> lotSizeFilter.minNotionalValue  (spot uses minOrderAmt)

All other keys and the output dict shape are identical so RiskLimits and format_qty/format_price
consume this dict unchanged.
"""

from __future__ import annotations


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_bybit_perp_instruments(payload: dict) -> dict[str, dict]:
    """Parse a Bybit linear-perp instruments-info payload into RiskLimits-shaped filter dicts."""
    out: dict[str, dict] = {}
    for entry in payload.get("result", {}).get("list", []):
        symbol = str(entry.get("symbol", "")).upper()
        if not symbol:
            continue
        pf = entry.get("priceFilter", {}) or {}
        lot = entry.get("lotSizeFilter", {}) or {}
        out[symbol] = {
            "tick_size": _f(pf, "tickSize"),
            "step_size": _f(lot, "qtyStep"),             # qtyStep, NOT basePrecision
            "min_qty": _f(lot, "minOrderQty"),
            "max_qty": _f(lot, "maxOrderQty"),
            "min_notional": _f(lot, "minNotionalValue"),  # minNotionalValue, NOT minOrderAmt
            "base_asset": str(entry.get("baseCoin", "")),
        }
    return out
