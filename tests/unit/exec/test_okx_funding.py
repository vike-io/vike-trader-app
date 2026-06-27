"""Tests for OKX funding via REST bills poller (decode_okx_funding_bills + OkxFundingPoller).

Verifies:
  - type=8 income bill -> FundingEvent with pnl as amount (received-positive; no sign flip)
  - type=8 expense bill -> negative amount
  - documented identity balChg == pnl for funding bill (fee==0)
  - pnl is PRIMARY, balChg is FALLBACK (pnl empty -> use balChg; balChg empty -> use pnl)
  - non-type-8 bills are skipped
  - zero-amount bills (pnl=0, balChg=0) are skipped
  - OkxFundingPoller dedup by billId: second poll of the same window does NOT re-publish
"""
from __future__ import annotations

from vike_trader_app.exec.okx.funding import decode_okx_funding_bills, OkxFundingPoller
from vike_trader_app.exec.events import FundingEvent


def _bill(**over):
    # A funding bill has fee==0, so balChg == pnl (verified identity balChg == fee + pnl). Carry BOTH
    # equal to document the convention; pnl is OKX's documented funding field (subType 174 income).
    b = {"billId": "b1", "instId": "BTC-USDT-SWAP", "type": "8", "subType": "174",
         "pnl": "0.91", "balChg": "0.91", "fee": "0", "ts": "1700000000000"}
    b.update(over)
    return b


def test_income_bill_is_received_positive():
    evs = decode_okx_funding_bills([_bill()], venue="okx", symbol="BTC-USDT-SWAP")
    assert [type(e).__name__ for e in evs] == ["FundingEvent"]
    ev = evs[0]
    assert isinstance(ev, FundingEvent)
    assert ev.amount == 0.91          # income, received-positive (no flip) — from pnl
    assert ev.position_side == "BOTH"
    assert ev.funding_rate == 0.0
    assert ev.mark_price is None
    assert ev.ts == 1700000000000


def test_expense_bill_is_negative():
    evs = decode_okx_funding_bills([_bill(subType="173", pnl="-1.40", balChg="-1.40")],
                                   venue="okx", symbol="BTC-USDT-SWAP")
    assert evs[0].amount == -1.40     # expense, paid (subType 173)


def test_funding_bill_identity_balchg_equals_pnl():
    # Documented + live-verified: a funding bill (fee==0) has balChg == pnl. The decoder uses pnl.
    b = _bill(pnl="0.55", balChg="0.55")
    assert b["pnl"] == b["balChg"]
    assert decode_okx_funding_bills([b], venue="okx", symbol="BTC-USDT-SWAP")[0].amount == 0.55


def test_pnl_is_primary_balchg_is_fallback():
    # pnl present -> used even if balChg differs (pnl is the OKX-documented funding field).
    only_pnl = _bill(pnl="0.91", balChg="")          # balChg empty -> pnl wins
    assert decode_okx_funding_bills([only_pnl], venue="okx", symbol="BTC-USDT-SWAP")[0].amount == 0.91
    only_balchg = _bill(pnl="", balChg="0.77")       # pnl empty -> balChg fallback
    assert decode_okx_funding_bills([only_balchg], venue="okx", symbol="BTC-USDT-SWAP")[0].amount == 0.77


def test_non_type8_bill_skipped():
    assert decode_okx_funding_bills([_bill(type="1")], venue="okx", symbol="BTC-USDT-SWAP") == []


def test_zero_amount_skipped():
    assert decode_okx_funding_bills([_bill(pnl="0", balChg="0")], venue="okx", symbol="BTC-USDT-SWAP") == []


# ---------------------------------------------------------------------------
# OkxFundingPoller — dedup by billId; apply_funding is NOT idempotent
# ---------------------------------------------------------------------------

class _SpyBus:
    def __init__(self): self.published = []
    def publish(self, ev): self.published.append(ev)


class _StubClient:
    VENUE = "okx"
    _base = "https://x"
    _signer = object()
    def __init__(self, bills): self._bills = bills
    def _transport(self, *a, **k): return {"code": "0", "data": self._bills}
    def unwrap(self, resp): return resp["data"]


def test_poller_publishes_once_per_billid():
    bus = _SpyBus()
    client = _StubClient([_bill(billId="b1"), _bill(billId="b1", pnl="0.5", balChg="0.5")])  # dup billId
    poller = OkxFundingPoller(bus=bus, client=client, symbol="BTC-USDT-SWAP")
    poller.poll()
    poller.poll()                       # re-poll the same window
    assert len(bus.published) == 1      # deduped-first by billId; apply_funding is NOT idempotent
    assert isinstance(bus.published[0], FundingEvent)
