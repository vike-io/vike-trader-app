"""OKX funding via REST bills — funding is NOT on ANY OKX WS channel.

GET /api/v5/account/bills?instType=SWAP&type=8 (Funding fee). The funding cashflow is in `pnl` (OKX's
DOCUMENTED funding field — docs note for subType 173/174: "refer to pnl for the fee payment"); a
funding bill has fee==0 so balChg == pnl (verified identity balChg == fee + pnl), and balChg is the
documented FALLBACK. Both are received-positive (subType 174 income > 0, 173 expense < 0) — fed
DIRECTLY into FundingEvent.amount (Account.apply_funding does balance += amount; accounting.py:60-63).
No funding rate in the bill -> funding_rate=0.0; no mark -> mark_price=None.

OkxFundingPoller polls on the MAIN THREAD (single-writer / data-layer rule), reusing the signed
transport already on OKXPerpExecutionClient. It DEDUPS-FIRST by billId (filters the raw bill list to
the UNSEEN funding rows BEFORE decode — no fragile zip post-filter) so re-polling the same window does
not double-fold (Account.apply_funding is NOT idempotent — every call shifts balance). The seen-set is
BOUNDED to the recent window so a long session does not grow it without limit.
"""
from __future__ import annotations

import logging

from vike_trader_app.exec.events import FundingEvent

_log = logging.getLogger(__name__)
_BILLS_PATH = "/api/v5/account/bills"
_FUNDING_BILL_TYPE = "8"
_SEEN_CAP = 4096   # bound the dedup set; OKX bills returns at most ~100 rows/page, far under the cap


def _funding_amount(bill) -> str | None:
    """Funding cashflow: pnl (documented) PRIMARY, balChg FALLBACK. None if neither is a real value."""
    chg = bill.get("pnl")
    if chg in (None, "", "0"):
        chg = bill.get("balChg")
    if chg in (None, "", "0"):
        return None
    return chg


def decode_okx_funding_bills(bills, *, venue: str = "okx", symbol: str = "") -> list[FundingEvent]:
    out: list[FundingEvent] = []
    for b in bills:
        if str(b.get("type", "")) != _FUNDING_BILL_TYPE:
            continue
        chg = _funding_amount(b)
        if chg is None:
            continue
        out.append(FundingEvent(
            venue=venue,
            symbol=str(b.get("instId", symbol)),
            position_side="BOTH",
            funding_rate=0.0,
            amount=float(chg),               # received-positive; income>0, expense<0 (pnl, else balChg)
            mark_price=None,
            ts=int(b.get("ts", 0) or 0),
        ))
    return out


class OkxFundingPoller:
    """Poll GET /api/v5/account/bills?type=8 on the main thread; publish FundingEvent (deduped by billId).

    Reuses the client's signed transport (client._transport, client._base, client._signer, .unwrap,
    .VENUE) so no new auth surface. Call .poll() from MainWindow's funding QTimer (main thread). Bills
    are DEDUPED-FIRST by billId — apply_funding is NOT idempotent, so a re-polled window must NOT
    re-publish. The seen-set is bounded to _SEEN_CAP.
    """

    def __init__(self, *, bus, client, symbol: str) -> None:
        self._bus = bus
        self._client = client
        self._symbol = symbol
        self._seen_bill_ids: set[str] = set()

    def _remember(self, bill_id: str) -> None:
        self._seen_bill_ids.add(bill_id)
        if len(self._seen_bill_ids) > _SEEN_CAP:
            # Drop the oldest half. Order is not strictly insertion-ordered for a set, but the cap only
            # bounds memory — a re-poll covers at most the last 7 days, far under the cap, so a dropped
            # id cannot be re-seen within a live window. Cheap, bounded, no double-fold in practice.
            for stale in list(self._seen_bill_ids)[: _SEEN_CAP // 2]:
                self._seen_bill_ids.discard(stale)

    def poll(self) -> None:
        try:
            resp = self._client._transport(
                self._client._base, _BILLS_PATH, "GET",
                {"instType": "SWAP", "type": _FUNDING_BILL_TYPE, "instId": self._symbol},
                self._client._signer)
            bills = self._client.unwrap(resp)
        except Exception:  # noqa: BLE001 — best-effort; a funding poll failure self-heals next poll
            _log.warning("okx funding bills poll failed (will retry next cadence)")
            return
        # DEDUP-FIRST: filter the raw bill list to the UNSEEN funding rows, mark them seen, THEN decode
        # the deduped subset (no fragile zip pairing of decoded events back to source rows).
        fresh: list[dict] = []
        for b in bills:
            if str(b.get("type", "")) != _FUNDING_BILL_TYPE or _funding_amount(b) is None:
                continue
            bill_id = str(b.get("billId", ""))
            if bill_id and bill_id in self._seen_bill_ids:
                continue
            if bill_id:
                self._remember(bill_id)
            fresh.append(b)
        for ev in decode_okx_funding_bills(fresh, venue=self._client.VENUE, symbol=self._symbol):
            self._bus.publish(ev)
