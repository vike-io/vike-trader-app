"""Decimal step/tick formatting avoids -1111 BAD_PRECISION (no IEEE artifacts in the REST payload)."""

from vike_trader_app.exec.binance.format import format_price, format_qty, format_to_step


def test_format_qty_quantizes_to_step():
    assert format_qty(0.30000000000000004, "0.001") == "0.300"
    assert format_qty(1.23456, "0.001") == "1.234"   # truncates (round-down) to step
    assert format_qty(2.0, "0.001") == "2.000"


def test_format_price_quantizes_to_tick():
    assert format_price(65432.17, "0.01") == "65432.17"
    assert format_price(65432.175, "0.1") == "65432.1"
    assert format_price(100.0, "1") == "100"


def test_no_scientific_notation_for_small_steps():
    assert format_qty(0.00012345, "0.00000001") == "0.00012345"
    assert "e" not in format_qty(0.00012345, "0.00000001").lower()


def test_format_to_step_zero_step_no_exception():
    """Missing LOT_SIZE/PRICE_FILTER gives step 0 -> must not raise ZeroDivisionError."""
    result = format_to_step(1.23456, 0.0)
    assert "E" not in result and "e" not in result  # no scientific notation
    assert "1.23456" in result  # value preserved as plain decimal
