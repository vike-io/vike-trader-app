"""Pure row<->spec conversion for the broker-profile editor table."""

from vike_trader_app.data.instruments import InstrumentSpec
from vike_trader_app.ui.profile_editor_data import COLUMNS, row_to_spec, spec_to_row


def test_columns_shape():
    assert COLUMNS == ["Symbol", "Asset", "Tick", "Pip", "Step", "Contract", "Decimals"]


def test_spec_to_row_is_compact_with_derived_decimals():
    s = InstrumentSpec("BTCUSDT", "crypto", 0.01, pip_size=0.01, volume_step=1e-5, contract_size=1.0)
    assert spec_to_row(s) == ["BTCUSDT", "crypto", "0.01", "0.01", "1e-05", "1", "2"]


def test_row_to_spec_parses_and_upcases_symbol():
    spec = row_to_spec(["eurusd", "forex", "0.00001", "0.0001", "0.01", "100000", "5"])
    assert spec.symbol == "EURUSD" and spec.asset_class == "forex"
    assert spec.tick_size == 0.00001 and spec.contract_size == 100000
    assert spec.decimals == 5            # derived from tick, not read off the (read-only) column


def test_row_to_spec_tolerates_blank_numeric_cells():
    spec = row_to_spec(["X", "", "", "", "", "", ""])
    assert spec.symbol == "X" and spec.asset_class == "crypto"
    assert spec.tick_size == 0.01 and spec.contract_size == 1.0


def test_row_spec_roundtrip():
    s = InstrumentSpec("AAPL", "stock", 0.01, pip_size=0.01, volume_step=1, contract_size=1.0)
    assert row_to_spec(spec_to_row(s)) == s
