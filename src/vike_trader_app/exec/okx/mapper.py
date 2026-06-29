"""Pure OKX private-WS -> vike event mapper (no socket; unit-tested with scripted frames).

OKX collapses Bybit's two-topic split (execution + order) into ONE 'orders' channel.
Each row carries BOTH the fill details AND the lifecycle state, so the mapper must handle both
in a single pass per row.

Output contract (mirrors bybit/mapper.map_execution EXACTLY for fill rows):
  fill row (fillSz>0 AND tradeId non-empty):
      -> [FillEvent, OrderFilled | OrderPartiallyFilled]
      The bare FillEvent folds the Account; the wrap drives the ManagedOrder FSM.
      These are NOT a double-fold (test_live_oms.py:60-70 proves it).
  no-fill lifecycle:
      state=='live'                           -> [OrderAccepted(venue_order_id=ordId)]
      state in ('canceled','mmp_canceled')    -> [OrderCanceled(reason=cancelSource)]
      state in ('filled','partially_filled')  -> []  (fill already folded via tradeId dedup)
  per-row code not in ('','0')               -> [OrderRejected(reason=msg)]

Commission: OKX fillFee is NEGATIVE for a charge; FillEvent.commission is SIGNED (>0 = charge/cost,
<0 = maker rebate/income). Implemented as -fillFee so a negative fillFee becomes positive cost.
Side: +1 for 'buy', -1 for 'sell'. Symbol: item.get('instId', symbol) — hub guards filtering.
"""

from __future__ import annotations

from vike_trader_app.exec.events import (
    AccountState,
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
)


def map_okx_order(item: dict, *, venue: str, symbol: str) -> list[object]:
    """One orders row -> [FillEvent?, lifecycle?].

    FillEvent is emitted ONLY when fillSz>0 AND tradeId is non-empty.
    When a fill is present, BOTH the bare FillEvent AND an OrderFilled/OrderPartiallyFilled
    wrap are returned — the bare event folds the Account, the wrap drives the FSM.
    """
    # Per-row error code check (defensive gate; takes precedence)
    code = str(item.get("code") or "")
    if code not in ("", "0"):
        coid = str(item.get("clOrdId", ""))
        ts = int(item.get("fillTime") or item.get("uTime") or 0)
        return [OrderRejected(
            client_order_id=coid,
            reason=str(item.get("msg", "")),
            ts=ts,
        )]

    state = str(item.get("state", ""))
    coid = str(item.get("clOrdId", ""))
    ts = int(item.get("fillTime") or item.get("uTime") or 0)

    # Determine whether this row carries a fill
    fill_sz_raw = str(item.get("fillSz") or "0")
    has_fill = fill_sz_raw not in ("", "0") and bool(item.get("tradeId"))

    if has_fill:
        fill = FillEvent(
            trade_id=str(item["tradeId"]),
            client_order_id=coid,
            venue=venue,
            symbol=str(item.get("instId", symbol)),
            side=+1 if item.get("side") == "buy" else -1,
            last_qty=float(item["fillSz"]),
            last_px=float(item["fillPx"]),
            commission=-float(item.get("fillFee") or 0),   # OKX fillFee<0 = charge -> positive cost; >0 = rebate -> negative
            liquidity_side="maker" if item.get("execType") == "M" else "taker",
            ts=ts,
        )

        # Determine terminality: primary = state=='filled'; fallback = accFillSz >= sz
        # Mirrors bybit/mapper.py: cumExecQty >= orderQty OR leavesQty == 0
        is_filled = state == "filled"
        if not is_filled:
            try:
                acc = float(item.get("accFillSz") or -1)
                sz = float(item.get("sz") or 0)
                if acc >= 0 and sz > 0 and acc >= sz:
                    is_filled = True
            except (TypeError, ValueError):
                pass

        wrap_cls = OrderFilled if is_filled else OrderPartiallyFilled
        return [fill, wrap_cls(client_order_id=coid, fill=fill, ts=ts)]

    # No fill — lifecycle-only path
    if state == "live":
        return [OrderAccepted(
            client_order_id=coid,
            venue_order_id=str(item.get("ordId", "")),
            ts=ts,
        )]
    if state in ("canceled", "mmp_canceled"):
        return [OrderCanceled(
            client_order_id=coid,
            reason=str(item.get("cancelSource", "")),
            ts=ts,
        )]
    # state in ('filled', 'partially_filled') with no fill: snapshot/dup — already folded
    return []


def map_okx_private(frame: dict, *, venue: str = "okx", symbol: str = "") -> list[object]:
    """Dispatch OKX private WS frame -> vike events.

    Handles:
      frame.get('event') present (login/subscribe/error ack) -> []
      non-dict (raw 'ping'/'pong' keepalive string)          -> []
      missing 'arg'                                          -> []
      arg.channel == 'orders'                               -> map_okx_order per data row
      any other channel                                      -> []
    """
    if not isinstance(frame, dict):
        return []
    if frame.get("event") is not None:
        return []
    arg = frame.get("arg")
    if not isinstance(arg, dict):
        return []
    channel = arg.get("channel")

    if channel == "account":
        # OKX account channel: data[].details[] with ccy/cashBal (TOTAL).
        # Identical to okx/perp_mapper.py — the account channel is the same for spot and swap.
        # One AccountState per frame. Default-safe: bad rows skipped silently.
        balances: list[tuple[str, float]] = []
        ts_frame = 0
        for entry in frame.get("data", []):
            try:
                ts_frame = int(entry.get("uTime", 0) or 0)
            except (TypeError, ValueError):
                pass
            for d in (entry.get("details") or []):
                try:
                    asset = str(d.get("ccy") or "")
                    wb = float(d.get("cashBal", 0) or 0)
                    if asset:
                        balances.append((asset, wb))
                except (TypeError, ValueError):
                    pass
        if balances:
            return [AccountState(venue=venue, balances=tuple(balances), ts=ts_frame)]
        return []

    if channel != "orders":
        return []

    events: list[object] = []
    for item in frame.get("data", []):
        events.extend(map_okx_order(item, venue=venue, symbol=symbol))
    return events
