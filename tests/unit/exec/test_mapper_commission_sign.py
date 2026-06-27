"""Cross-venue mapper commission-sign tests.

Convention (FillEvent.commission):
  > 0 = charge / cost   (taker fee, etc.)
  < 0 = maker rebate / income

This file:
1. Asserts the signed fix for OKX (fillFee<0 = charge) and Deribit (fee<0 = maker rebate).
2. Confirms that Binance (n) and Bybit (execFee) already emit positive commission for a charge
   with no code change needed.
"""
from __future__ import annotations

import pytest

from vike_trader_app.exec.okx.mapper import map_okx_order
from vike_trader_app.exec.deribit.mapper import map_deribit_trade
from vike_trader_app.exec.binance.mapper import map_execution_report
from vike_trader_app.exec.bybit.mapper import map_execution
from vike_trader_app.exec.events import FillEvent


# ---------------------------------------------------------------------------
# OKX helpers
# ---------------------------------------------------------------------------

def _okx_row(**kw) -> dict:
    """Minimal valid OKX orders-channel fill row (fillSz>0, tradeId non-empty)."""
    row = {
        "instId": "BTC-USDT",
        "ordId": "ord-okx-1",
        "clOrdId": "coid-okx-1",
        "side": "buy",
        "ordType": "limit",
        "sz": "0.001",
        "px": "50000",
        "state": "filled",
        "fillSz": "0.001",
        "fillPx": "50000",
        "fillFee": "-0.7",          # OKX: negative = charge
        "fillTime": "1700000000000",
        "tradeId": "T-okx-1",
        "execType": "T",            # taker
        "accFillSz": "0.001",
        "code": "",
        "msg": "",
        "cancelSource": "",
        "uTime": "1700000000000",
    }
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
# Deribit helpers
# ---------------------------------------------------------------------------

def _drb_row(**kw) -> dict:
    """Minimal valid Deribit user.trades trade row."""
    row = {
        "trade_id": "drb-trade-1",
        "order_id": "drb-ord-1",
        "instrument_name": "BTC-25SEP20-9000-C",
        "direction": "buy",
        "amount": 1.0,
        "price": 0.025,
        "fee": 0.5,                 # Deribit: positive = charge
        "fee_currency": "BTC",
        "liquidity": "T",           # taker
        "state": "filled",
        "timestamp": 1590484255886,
        "label": "coid-drb-1",
    }
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
# OKX tests (SIGNED fix)
# ---------------------------------------------------------------------------

def test_okx_charge_is_positive_commission():
    """OKX fillFee is NEGATIVE for a charge; mapped commission must be POSITIVE (cost)."""
    row = _okx_row(fillFee="-0.7")
    evs = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    fill = next(e for e in evs if isinstance(e, FillEvent))
    assert fill.commission == pytest.approx(0.7)


def test_okx_rebate_is_negative_commission():
    """OKX fillFee is POSITIVE for a maker rebate; mapped commission must be NEGATIVE (income)."""
    row = _okx_row(fillFee="0.3", execType="M")
    evs = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    fill = next(e for e in evs if isinstance(e, FillEvent))
    assert fill.commission == pytest.approx(-0.3)


def test_okx_zero_fee_stays_zero():
    """fillFee of '0' or None stays 0.0 commission."""
    for fee_val in ("0", "", None):
        row = _okx_row(fillFee=fee_val)
        evs = map_okx_order(row, venue="okx", symbol="BTC-USDT")
        fill = next(e for e in evs if isinstance(e, FillEvent))
        assert fill.commission == 0.0, f"Expected 0.0 for fillFee={fee_val!r}, got {fill.commission}"


# ---------------------------------------------------------------------------
# Deribit tests (SIGNED fix)
# ---------------------------------------------------------------------------

def test_deribit_charge_is_positive_commission():
    """Deribit fee > 0 = taker charge; must stay positive."""
    evs = map_deribit_trade(_drb_row(fee=0.5), venue="deribit", symbol="BTC-25SEP20-9000-C")
    fill = next(e for e in evs if isinstance(e, FillEvent))
    assert fill.commission == pytest.approx(0.5)


def test_deribit_rebate_is_negative_commission():
    """Deribit fee < 0 = maker rebate; must remain negative (income)."""
    evs = map_deribit_trade(_drb_row(fee=-0.2), venue="deribit", symbol="BTC-25SEP20-9000-C")
    fill = next(e for e in evs if isinstance(e, FillEvent))
    assert fill.commission == pytest.approx(-0.2)


def test_deribit_zero_fee_stays_zero():
    """fee=0 stays 0.0."""
    evs = map_deribit_trade(_drb_row(fee=0), venue="deribit", symbol="BTC-25SEP20-9000-C")
    fill = next(e for e in evs if isinstance(e, FillEvent))
    assert fill.commission == 0.0


# ---------------------------------------------------------------------------
# Binance confirm (no code change — already correct)
# ---------------------------------------------------------------------------

def _binance_trade_row(**kw) -> dict:
    """Minimal valid Binance executionReport TRADE frame."""
    row = {
        "e": "executionReport",
        "s": "BTCUSDT",
        "c": "coid-bn-1",
        "T": 1700000000000,
        "x": "TRADE",
        "X": "FILLED",
        "t": "bn-trade-1",
        "S": "BUY",
        "l": "0.001",           # last filled qty
        "L": "50000",           # last filled price
        "n": "0.05",            # commission (Binance n > 0 = charge)
        "m": False,             # is maker
        "i": "bn-ord-1",
    }
    row.update(kw)
    return row


def test_binance_charge_is_positive_commission():
    """Binance 'n' is already positive for a taker charge — confirmed no code change needed."""
    frame = _binance_trade_row(n="0.05")
    evs = map_execution_report(frame, venue="binance", symbol="BTCUSDT")
    fill = next(e for e in evs if isinstance(e, FillEvent))
    assert fill.commission > 0
    assert fill.commission == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Bybit confirm (no code change — already correct)
# ---------------------------------------------------------------------------

def _bybit_exec_row(**kw) -> dict:
    """Minimal valid Bybit execution row (execType='Trade')."""
    row = {
        "execType": "Trade",
        "execId": "bb-exec-1",
        "orderLinkId": "coid-bb-1",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "execQty": "0.001",
        "execPrice": "50000",
        "execFee": "0.05",      # Bybit execFee > 0 = charge
        "isMaker": False,
        "execTime": "1700000000000",
        "cumExecQty": "0.001",
        "orderQty": "0.001",
        "leavesQty": "0",
    }
    row.update(kw)
    return row


def test_bybit_charge_is_positive_commission():
    """Bybit 'execFee' is already positive for a taker charge — confirmed no code change needed."""
    row = _bybit_exec_row(execFee="0.05")
    evs = map_execution(row, venue="bybit", symbol="BTCUSDT")
    fill = next(e for e in evs if isinstance(e, FillEvent))
    assert fill.commission > 0
    assert fill.commission == pytest.approx(0.05)
