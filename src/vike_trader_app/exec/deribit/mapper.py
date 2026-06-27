"""Pure Deribit private-WS user.trades -> vike event mapper (no socket; scripted-frame unit-tested).

Deribit's user.trades.{kind}.{currency}.{interval} channel is FILLS-ONLY (params.data is a LIST of
trade objects). Order lifecycle (Submitted/Accepted/Rejected) is published synchronously by
DeribitExecutionClient.submit on the order path (deribit/client.py:69-82), so 6b subscribes ONLY to
user.trades and this mapper has NO lifecycle branch — closest to bybit/mapper.map_execution wrapped
in an OKX-style dispatch guard.

Output contract (dual-publish, mirrors bybit/okx for fill rows):
  one trade row -> [FillEvent, OrderFilled | OrderPartiallyFilled]
  The bare FillEvent folds the Account; the wrap drives the ManagedOrder FSM (evs[1].fill is evs[0]).
  These are NOT a double-fold (test_live_oms.py proves it).

Terminality: is_filled = (trade['state'] == 'filled'). user.trades carries no cumQty/leavesQty, so the
OKX/Bybit accFillSz>=sz fallback is N/A — state=='filled' is the sole signal; an intermediate partial
reads state=='open' -> OrderPartiallyFilled (the FSM tolerates a missing-terminal; apply is idempotent).

Commission: Deribit 'fee' can be NEGATIVE (maker rebate); FillEvent.commission is the abs value.
Side: +1 for 'buy', -1 for 'sell'. Symbol: item.get('instrument_name', symbol) — hub guards filtering.
client_order_id = item.get('label', '') (Deribit 'label' IS the client order id; tolerate absence).
Options are one-way -> position_side stays the default 'BOTH' (spot-identical; no perp_mapper variant).
amount is COIN units for options -> last_qty = float(amount) directly (no ct_val rescale).
"""
from __future__ import annotations

from vike_trader_app.exec.events import FillEvent, OrderFilled, OrderPartiallyFilled


def map_deribit_trade(item: dict, *, venue: str, symbol: str) -> list[object]:
    """One user.trades trade row -> [FillEvent, OrderFilled | OrderPartiallyFilled]."""
    coid = str(item.get("label", ""))
    ts = int(item.get("timestamp", 0) or 0)

    fill = FillEvent(
        trade_id=str(item.get("trade_id", "")),
        client_order_id=coid,
        venue=venue,
        symbol=str(item.get("instrument_name", symbol)),
        side=+1 if item.get("direction") == "buy" else -1,
        last_qty=float(item.get("amount", 0) or 0),          # COIN units (options) — no rescale
        last_px=float(item.get("price", 0) or 0),
        commission=abs(float(item.get("fee", 0) or 0)),      # fee can be negative (maker rebate)
        liquidity_side="maker" if item.get("liquidity") == "M" else "taker",
        ts=ts,
    )

    is_filled = str(item.get("state", "")) == "filled"
    wrap_cls = OrderFilled if is_filled else OrderPartiallyFilled
    return [fill, wrap_cls(client_order_id=coid, fill=fill, ts=ts)]


def map_deribit_private(frame, *, venue: str = "deribit", symbol: str = "") -> list[object]:
    """Dispatch a Deribit private WS frame -> vike events.

    Handles:
      non-dict (raw keepalive string / None)                 -> []
      frame.get('method') != 'subscription' (auth/sub ack,
        heartbeat, any RPC reply carrying 'id'+'result')      -> []
      params.channel not starting with 'user.trades'          -> []
      params.data not a list (other user.* channels send dict)-> []
      otherwise: map_deribit_trade per row in params.data
    """
    if not isinstance(frame, dict):
        return []
    if frame.get("method") != "subscription":
        return []
    params = frame.get("params")
    if not isinstance(params, dict):
        return []
    channel = params.get("channel", "")
    if not (isinstance(channel, str) and channel.startswith("user.trades")):
        return []
    data = params.get("data")
    if not isinstance(data, list):
        return []

    events: list[object] = []
    for item in data:
        events.extend(map_deribit_trade(item, venue=venue, symbol=symbol))
    return events
