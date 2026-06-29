"""Pure Bybit private-WS -> vike event mapper (no socket; unit-tested with scripted frames).

Dispatches on frame['topic']:
  'execution' rows -> [FillEvent, OrderPartiallyFilled|OrderFilled]  (dual-publish, mirrors Binance)
  'order' rows    -> lifecycle ONLY (NEVER fills; Filled/PartiallyFilled -> [] to avoid double-fold)
  op-only ack frames (pong/auth/subscribe, no 'topic') -> []

Commission is carried on FillEvent.commission, NEVER netted into last_px.
trade_id = execId (the dedup key across reconnects); client_order_id = orderLinkId.

SELL sign: side maps +1 for 'Buy', -1 for 'Sell'; last_qty is always positive.
leavesQty terminal: OrderFilled when cumExecQty >= orderQty (robust to string-dust) OR leavesQty
parses to exactly 0.0; otherwise OrderPartiallyFilled.

order-topic 'New' -> OrderAccepted: the REST client already published this synchronously before any
WS frame arrives, so LiveOmsHub will swallow the resulting duplicate InvalidOrderTransition. The
decoder emits it faithfully; swallowing is the hub's concern, not the mapper's.

fill-on-INITIALIZED precondition: this decoder is pure. In live use, the order must already be in
ACCEPTED state (guaranteed by the synchronous REST submit) before any execution frame arrives. See
Task-5 live_oms integration tests for the contract assertion.
"""

from __future__ import annotations

from vike_trader_app.exec.events import (
    AccountState,
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderExpired,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
)


def map_execution(item: dict, *, venue: str, symbol: str) -> list[object]:
    """One execution row -> [FillEvent, OrderPartiallyFilled|OrderFilled].

    execType != 'Trade' -> [] (skip BustTrade/Funding/AdlTrade/SettleFundingFee on spot).
    orderLinkId == '' is allowed (FillEvent still built; registry lookup no-ops downstream).
    """
    if item.get("execType") != "Trade":
        return []

    coid = str(item.get("orderLinkId", ""))
    ts = int(item.get("execTime", 0))

    fill = FillEvent(
        trade_id=str(item.get("execId", "")),
        client_order_id=coid,
        venue=venue,
        symbol=str(item.get("symbol", symbol)),
        side=+1 if item.get("side") == "Buy" else -1,
        last_qty=float(item.get("execQty", 0) or 0),
        last_px=float(item.get("execPrice", 0) or 0),
        commission=float(item.get("execFee", 0) or 0),
        liquidity_side="maker" if item.get("isMaker") else "taker",
        ts=ts,
    )

    # Determine terminal status robustly (leavesQty is a STRING from Bybit and can carry dust).
    # Primary signal: cumExecQty >= orderQty (most reliable when both fields present).
    # Fallback: leavesQty parses to exactly 0.0.
    try:
        cum_exec_qty = float(item.get("cumExecQty", -1))
        order_qty = float(item.get("orderQty", -1))
        if cum_exec_qty >= 0 and order_qty > 0 and cum_exec_qty >= order_qty:
            is_filled = True
        else:
            is_filled = float(item.get("leavesQty", 1) or 1) == 0.0
    except (TypeError, ValueError):
        is_filled = False

    wrap_cls = OrderFilled if is_filled else OrderPartiallyFilled
    return [fill, wrap_cls(client_order_id=coid, fill=fill, ts=ts)]


def map_order(item: dict, *, venue: str, symbol: str) -> list[object]:
    """One order row -> lifecycle ONLY (NEVER fills):
       New                   -> OrderAccepted(venue_order_id=orderId)
       Cancelled             -> OrderCanceled(reason=cancelType)
       PartiallyFilledCanceled -> OrderCanceled(reason=cancelType)
       Rejected              -> OrderRejected(reason=rejectReason)
       Deactivated           -> OrderExpired
       Filled / PartiallyFilled -> []  (already covered by the execution topic — no double-fold)
       else                  -> []
    """
    status = item.get("orderStatus", "")
    coid = str(item.get("orderLinkId", ""))
    ts = int(item.get("updatedTime", 0))

    if status == "New":
        # LiveOmsHub will swallow the duplicate InvalidOrderTransition (REST already published this).
        return [OrderAccepted(
            client_order_id=coid,
            venue_order_id=str(item.get("orderId", "")),
            ts=ts,
        )]
    if status in ("Cancelled", "PartiallyFilledCanceled"):
        return [OrderCanceled(
            client_order_id=coid,
            reason=str(item.get("cancelType", "")),
            ts=ts,
        )]
    if status == "Rejected":
        return [OrderRejected(
            client_order_id=coid,
            reason=str(item.get("rejectReason", "")),
            ts=ts,
        )]
    if status == "Deactivated":
        return [OrderExpired(client_order_id=coid, ts=ts)]
    # Filled / PartiallyFilled -> [] (execution topic handles fills; no double-fold)
    return []


def map_bybit_private(frame: dict, *, venue: str = "bybit", symbol: str = "") -> list[object]:
    """Dispatch on frame['topic']; iterate frame['data']; [] for op-only ack/pong frames.

    Handles:
      topic='execution' -> map_execution per row
      topic='order'     -> map_order per row
      op='pong'/'auth'/'subscribe' (no topic) -> []
      anything else     -> []
    """
    topic = frame.get("topic")
    if topic is None:
        # op-only ack frame (pong, auth, subscribe) — no data to process
        return []

    data: list[dict] = frame.get("data", [])
    events: list[object] = []

    if topic == "execution":
        for item in data:
            events.extend(map_execution(item, venue=venue, symbol=symbol))
    elif topic == "order":
        for item in data:
            events.extend(map_order(item, venue=venue, symbol=symbol))
    elif topic == "wallet":
        # Bybit V5 unified-account wallet push: data[].coin[] with coin/walletBalance (TOTAL).
        # Identical to bybit/perp_mapper.py — the wallet topic is the same for spot and linear.
        # One AccountState per frame covering all coin rows from all account entries.
        # Default-safe: malformed coin/walletBalance entries are skipped silently.
        ts = int(frame.get("creationTime", 0) or 0)
        balances: list[tuple[str, float]] = []
        for acct in data:
            for c in (acct.get("coin") or []):
                try:
                    asset = str(c.get("coin") or "")
                    wb = float(c.get("walletBalance", 0) or 0)
                    if asset:
                        balances.append((asset, wb))
                except (TypeError, ValueError):
                    pass
        if balances:
            events.append(AccountState(venue=venue, balances=tuple(balances), ts=ts))
    # else: unknown topic (position, etc.) -> []

    return events
