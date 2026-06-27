"""Pure parse of a Deribit public/get_instruments (kind=option) payload into the RiskLimits-shaped
filters dict the gate consumes — the SAME keys parse_okx_instruments emits (tick_size/step_size/
min_qty/max_qty/min_notional) plus contract_size + base_asset.

Deribit options: amount is in COIN units; min_trade_amount is the qty granularity (step_size). There is
no per-instrument max or notional floor in this payload, so max_qty/min_notional default to 0.0 (the
"no cap" convention, matching parse_okx_instruments). base_asset reuses the read-side parser
(data/options/deribit.py:44 parse_instrument_name) so exec and data share ONE regex.
"""
from __future__ import annotations

from vike_trader_app.data.options.deribit import parse_instrument_name


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_deribit_option_instruments(payload: dict) -> dict[str, dict]:
    """Return {instrument_name: filters dict} for every option row in a get_instruments payload."""
    out: dict[str, dict] = {}
    for entry in payload.get("result", []):
        if str(entry.get("kind", "")) != "option":
            continue
        name = str(entry.get("instrument_name", ""))
        parsed = parse_instrument_name(name)
        if parsed is None:
            continue
        out[name] = {
            "tick_size": _f(entry, "tick_size"),
            "step_size": _f(entry, "min_trade_amount"),
            "min_qty": _f(entry, "min_trade_amount"),
            "max_qty": 0.0,
            "min_notional": 0.0,
            "contract_size": _f(entry, "contract_size"),
            "base_asset": parsed[0],
        }
    return out
