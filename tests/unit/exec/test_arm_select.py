"""Unit tests for exec.arm_select — the Qt-free pick->arm bridge (6e)."""
from vike_trader_app.exec.arm_select import ExecArmSelection, pick_to_arm_selection


def test_valid_btc_option_maps_to_deribit_option_selection():
    sel = pick_to_arm_selection("BTC-27JUN26-100000-C")
    assert sel == ExecArmSelection(venue="deribit", product="Option",
                                   symbol="BTC-27JUN26-100000-C")


def test_valid_sol_usdc_option_keeps_exact_name():
    sel = pick_to_arm_selection("SOL_USDC-26JUN26-90-P")
    assert sel is not None
    assert sel.symbol == "SOL_USDC-26JUN26-90-P"   # _USDC suffix preserved verbatim


def test_non_option_name_returns_none():
    assert pick_to_arm_selection("BTCUSDT") is None        # spot ticker, not an option
    assert pick_to_arm_selection("BTC-PERPETUAL") is None  # perp, not an option
    assert pick_to_arm_selection("") is None
    assert pick_to_arm_selection(None) is None


def test_eth_option_maps_to_deribit():
    sel = pick_to_arm_selection("ETH-27JUN26-3000-C")
    assert sel is not None
    assert sel.venue == "deribit"
    assert sel.product == "Option"
    assert sel.symbol == "ETH-27JUN26-3000-C"


def test_selection_is_frozen_dataclass():
    sel = pick_to_arm_selection("BTC-27JUN26-100000-P")
    assert sel is not None
    # frozen dataclass — cannot mutate
    import dataclasses
    assert dataclasses.is_dataclass(sel)
    try:
        sel.venue = "other"  # type: ignore[misc]
        assert False, "should have raised FrozenInstanceError"
    except Exception:
        pass
