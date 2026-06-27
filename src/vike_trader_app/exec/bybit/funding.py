"""Bybit funding via REST transaction-log — the documented received-positive source.

GET /v5/account/transaction-log?category=linear&type=SETTLEMENT. The 'funding' field is the signed
cashflow (received-positive: + received, − paid — doc verbatim) fed DIRECTLY into FundingEvent.amount
(Account.apply_funding does balance += amount; accounting.py:60-63). NOT the WS execution-topic
'Funding' row, whose execFee is the EXACT NEGATIVE of this cashflow. transaction-log has NO markPrice
(mark_price=None); feeRate IS the funding rate.

BybitFundingPoller polls on the MAIN THREAD (single-writer / data-layer rule), reusing the signed
transport on BybitPerpExecutionClient, and DEDUPS-FIRST by row 'id' (apply_funding is NOT idempotent).
The endpoint caps the window to 7 days — page within ≤7-day windows.
"""
from __future__ import annotations

import logging

from vike_trader_app.exec.bybit.transport import bybit_signed_request
from vike_trader_app.exec.events import FundingEvent

_log = logging.getLogger(__name__)
_LOG_PATH = "/v5/account/transaction-log"
_SETTLEMENT = "SETTLEMENT"
_MAX_SEEN = 4096   # bounded dedup set to avoid unbounded growth on long-running sessions


def decode_bybit_funding_settlements(rows, *, venue: str = "bybit", symbol: str = "") -> list[FundingEvent]:
    """Decode SETTLEMENT rows from /v5/account/transaction-log into FundingEvent list.

    - Skips non-SETTLEMENT rows.
    - Skips rows where funding is None, empty string, or "0".
    - amount = row['funding'] DIRECTLY (received-positive; no sign flip).
    - mark_price = None (transaction-log carries no markPrice).
    """
    out: list[FundingEvent] = []
    for r in rows:
        if str(r.get("type", "")) != _SETTLEMENT:
            continue
        funding = r.get("funding")
        if funding in (None, "", "0"):
            continue
        out.append(FundingEvent(
            venue=venue,
            symbol=str(r.get("symbol", symbol)),
            position_side="BOTH",
            funding_rate=float(r.get("feeRate", 0) or 0),
            amount=float(funding or 0),        # received-positive; + received, − paid (verified live)
            mark_price=None,                   # transaction-log has no markPrice
            ts=int(r.get("transactionTime", 0) or 0),
        ))
    return out


class BybitFundingPoller:
    """Main-thread REST poller for Bybit linear-perp funding settlements.

    Deduplicates by row 'id' BEFORE decoding — apply_funding is NOT idempotent. Polls
    GET /v5/account/transaction-log?category=linear&type=SETTLEMENT, publishes each new
    FundingEvent to the bus.

    Parameters
    ----------
    bus:
        Event bus with a ``publish(event)`` method (called on the main thread).
    client:
        A BybitPerpExecutionClient instance (provides ``._base``, ``._signer``, ``unwrap()``).
    symbol:
        The canonical symbol string (e.g. ``"BTCUSDT"``).
    _transport:
        Injectable signed transport (default: ``bybit_signed_request``). Pass a mock in tests.
    """

    def __init__(self, *, bus, client, symbol: str,
                 _transport=bybit_signed_request) -> None:
        self._bus = bus
        self._client = client
        self._symbol = symbol
        self._transport = _transport
        self._seen_ids: set[str] = set()

    def poll(self) -> None:
        """Fetch the latest SETTLEMENT rows and publish new FundingEvents (dedup by 'id')."""
        try:
            raw = self._client.unwrap(
                self._transport(
                    self._client._base,
                    _LOG_PATH,
                    "GET",
                    {"category": "linear", "type": _SETTLEMENT},
                    self._client._signer,
                )
            )
        except Exception:  # noqa: BLE001
            _log.exception("BybitFundingPoller: transaction-log fetch failed")
            return

        rows = raw.get("list", []) if isinstance(raw, dict) else []
        new_rows = []
        for r in rows:
            rid = str(r.get("id", ""))
            if rid and rid not in self._seen_ids:
                new_rows.append(r)

        if not new_rows:
            return

        events = decode_bybit_funding_settlements(new_rows, venue="bybit", symbol=self._symbol)
        for ev in events:
            self._bus.publish(ev)

        # Mark as seen AFTER publishing (so a publish error doesn't silently drop events)
        for r in new_rows:
            rid = str(r.get("id", ""))
            if rid:
                self._seen_ids.add(rid)

        # Bound the seen-set to avoid unbounded growth
        if len(self._seen_ids) > _MAX_SEEN:
            # Drop oldest entries (sets don't have order, so just trim to half)
            excess = list(self._seen_ids)[: len(self._seen_ids) - _MAX_SEEN // 2]
            self._seen_ids -= set(excess)
