"""Qt-free row<->spec conversion for the broker-profile editor table.

Kept out of the widget (``profile_editor.py``) so the parsing is unit-testable, matching the
``datamanager_data`` convention. ``Decimals`` is a derived, read-only display column (computed
from the tick), so it never round-trips back into the spec as an override.
"""

from ..data.instruments import ASSET_CRYPTO, InstrumentSpec

COLUMNS = ["Symbol", "Asset", "Tick", "Pip", "Step", "Contract", "Decimals"]
_EDITABLE = 6  # columns 0..5 are editable; "Decimals" (6) is derived/read-only


def _g(x: float) -> str:
    """Compact float -> str (``0.01`` not ``0.010000``, ``100000`` not ``100000.0``)."""
    return f"{x:g}"


def spec_to_row(spec: InstrumentSpec) -> list[str]:
    """One editor row from a spec: symbol, asset, tick, pip, step, contract, derived decimals."""
    return [spec.symbol, spec.asset_class, _g(spec.tick_size), _g(spec.pip_size),
            _g(spec.volume_step), _g(spec.contract_size), str(spec.decimals)]


def _f(cell: str, default: float = 0.0) -> float:
    try:
        return float(str(cell).strip())
    except (ValueError, TypeError):
        return default


def row_to_spec(cells: list[str]) -> InstrumentSpec:
    """Parse an editor row back into a spec (price_decimals stays derived from the tick)."""
    return InstrumentSpec(
        symbol=str(cells[0]).strip().upper(),
        asset_class=(str(cells[1]).strip() or ASSET_CRYPTO),
        tick_size=_f(cells[2], 0.01),
        pip_size=_f(cells[3], _f(cells[2], 0.01)),
        volume_step=_f(cells[4], 0.0),
        contract_size=_f(cells[5], 1.0),
    )
