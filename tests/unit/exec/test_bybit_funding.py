"""Tests for the Bybit funding decoder + poller (Task 1B).

Verifies:
  - received-positive funding flows DIRECTLY to FundingEvent.amount (no sign flip)
  - negative (paid) funding passes through unchanged
  - non-SETTLEMENT rows are skipped
  - zero-funding rows are skipped
  - poller deduplicates by row 'id' (apply_funding is NOT idempotent)
"""
from __future__ import annotations

from vike_trader_app.exec.bybit.funding import decode_bybit_funding_settlements, BybitFundingPoller
from vike_trader_app.exec.events import FundingEvent


def _row(**over):
    r = {"id": "1", "type": "SETTLEMENT", "symbol": "BTCUSDT", "funding": "0.42",
         "feeRate": "0.0001", "transactionTime": "1700000000000"}
    r.update(over)
    return r


def test_received_funding_is_positive():
    evs = decode_bybit_funding_settlements([_row()], venue="bybit", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["FundingEvent"]
    ev = evs[0]
    assert isinstance(ev, FundingEvent)
    assert ev.amount == 0.42           # received-positive (no flip) — verified live
    assert ev.funding_rate == 0.0001
    assert ev.mark_price is None       # transaction-log carries NO markPrice
    assert ev.position_side == "BOTH"
    assert ev.ts == 1700000000000


def test_paid_funding_is_negative():
    evs = decode_bybit_funding_settlements([_row(funding="-0.66")], venue="bybit", symbol="BTCUSDT")
    assert evs[0].amount == -0.66      # negative = paid (verified live: ONDOUSDT -0.66563622)


def test_non_settlement_row_skipped():
    assert decode_bybit_funding_settlements([_row(type="TRADE")], venue="bybit", symbol="BTCUSDT") == []


def test_zero_funding_skipped():
    assert decode_bybit_funding_settlements([_row(funding="0")], venue="bybit", symbol="BTCUSDT") == []


def test_poller_publishes_once_per_id():
    """Same row id fed twice must produce exactly ONE FundingEvent (dedup; apply_funding is NOT idempotent)."""
    published = []

    class _Bus:
        def publish(self, ev):
            published.append(ev)

    class _Client:
        def __init__(self):
            self._base = "https://api.bybit.com"
            self._signer = None

        def unwrap(self, resp):
            return resp

    calls = [0]

    def _transport(base, path, method, params, signer, **kw):
        calls[0] += 1
        # The real bybit_signed_request returns the raw envelope; client.unwrap() strips it.
        # Mock unwrap returns resp unchanged, so return the already-unwrapped result level.
        return {"list": [_row(id="row-1")]}

    poller = BybitFundingPoller(
        bus=_Bus(),
        client=_Client(),
        symbol="BTCUSDT",
        _transport=_transport,
    )
    poller.poll()   # first poll: should publish 1 event
    poller.poll()   # second poll: same id -> deduped, no extra publish

    assert len(published) == 1
    assert isinstance(published[0], FundingEvent)
    assert published[0].amount == 0.42
