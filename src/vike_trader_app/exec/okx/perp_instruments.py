"""Pure parse of an OKX V5 /api/v5/public/instruments (instType=SWAP) payload into the same
RiskLimits-shaped filters dict the spot path builds (parse_okx_instruments), plus base_asset,
ct_val (contract value in base coin), and ct_mult (contract multiplier).

OKX SWAP gives lotSz (qty step in CONTRACTS), tickSz (price step), minSz (min order qty in CONTRACTS),
maxMktSz (max mkt qty), ctVal (base coin per 1 contract), ctMult (contract multiplier), and
ctValCcy (the base coin the ctVal is denominated in).

There is no explicit min_notional for OKX SWAP so it defaults to 0.0.
Same dict keys so RiskLimits and format_qty/format_price consume it unchanged.
"""

from __future__ import annotations


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_okx_perp_instruments(payload: dict) -> dict[str, dict]:
    """Return a mapping of OKX instId -> filters dict (same shape as Bybit perp parser, plus ct_val/ct_mult)."""
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
            "base_asset": str(entry.get("ctValCcy", "")),
            "ct_val": _f(entry, "ctVal"),
            "ct_mult": _f(entry, "ctMult"),
        }
    return out
