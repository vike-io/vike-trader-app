"""LIVE smoke against api-demo.bybit.com — manual gate, excluded by -m "not network".

Requires BYBIT_DEMO_API_KEY/SECRET in .env. Places a small resting BTCUSDT BUY LIMIT at 90% of the
current market price (fetched live) so it rests without filling; confirms OrderAccepted via the REST
ACK, cancels it (swallowing retCode 110001), then re-reconciles to prove it is gone.

Run manually:
    PYTHONPATH=src .venv/Scripts/python -m pytest tests/unit/exec/test_bybit_live_smoke.py -m network -v
"""

from __future__ import annotations

import os
import time

import pytest

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]

    load_dotenv("C:/Projects/vike-trader-app/.env", override=False)
except ImportError:
    pass

pytestmark = pytest.mark.network


def _creds_present() -> bool:
    return bool(os.environ.get("BYBIT_DEMO_API_KEY") and os.environ.get("BYBIT_DEMO_API_SECRET"))


@pytest.mark.skipif(not _creds_present(), reason="BYBIT_DEMO_API_KEY/SECRET not set in .env")
def test_bybit_demo_round_trip_place_then_cancel() -> None:
    """Connect/reconcile -> place small resting BUY LIMIT -> verify ACK -> cancel -> cleanup.

    Step 1 — resolve_venue_config: proves creds load and VenueConfig builds for bybit/DEMO.
    Step 2 — instruments-info: unsigned GET, parses BTCUSDT filters (tick/step/min_notional).
    Step 3 — connect(): signs GET /v5/account/wallet-balance + /v5/order/realtime, returns ReconcileSnapshot.
    Step 4 — submit(): signs POST /v5/order/create, publishes OrderSubmitted then OrderAccepted.
    Step 5 — cancel(): signs POST /v5/order/cancel, swallows retCode 110001 if already gone.
    Step 6 — final connect(): re-reconcile proves the resting order is gone (idempotency check).
    """
    from decimal import ROUND_DOWN, Decimal

    from vike_trader_app.exec.binance.transport import get_public_json
    from vike_trader_app.exec.bybit.client import BybitSpotExecutionClient
    from vike_trader_app.exec.bybit.instruments import parse_bybit_instruments_info
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.coid import CoidMinter
    from vike_trader_app.exec.credentials import Environment
    from vike_trader_app.exec.events import OrderAccepted, OrderRejected, OrderRequest
    from vike_trader_app.exec.venue_config import resolve_venue_config

    # --- Step 1: resolve venue config ----------------------------------------------------------
    cfg = resolve_venue_config("bybit", Environment.DEMO, now_ms=lambda: int(time.time() * 1000))
    assert cfg is not None, "creds present but venue config did not resolve"
    assert cfg.rest_base_url == "https://api-demo.bybit.com", f"unexpected REST: {cfg.rest_base_url}"

    # --- Step 2: fetch instruments-info (unsigned) ---------------------------------------------
    info = get_public_json(cfg.rest_base_url, "/v5/market/instruments-info",
                           {"category": "spot", "symbol": "BTCUSDT"})
    parsed = parse_bybit_instruments_info(info)
    assert "BTCUSDT" in parsed, f"BTCUSDT not in instruments-info: {list(parsed)[:5]}"
    f = parsed["BTCUSDT"]
    base_asset = f["base_asset"]
    filters = {k: v for k, v in f.items() if k != "base_asset"}

    # --- wire up the execution client ----------------------------------------------------------
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe(seen.append)
    client = BybitSpotExecutionClient(bus, signer=cfg.signer, rest_base_url=cfg.rest_base_url,
                                      symbol="BTCUSDT", filters=filters, base_asset=base_asset)

    # --- Step 3: connect / reconcile -----------------------------------------------------------
    snapshot = client.connect()
    assert snapshot.positions, "connect() returned empty positions tuple"
    assert snapshot.positions[0][0] == "BTCUSDT", (
        f"unexpected symbol in snapshot: {snapshot.positions[0][0]}"
    )
    # CRITICAL regression guard: walletBalance on the real demo is ~1.0 BTC, NOT 0.
    # A seed of 0 means iter_balances is reading availableToWithdraw (deprecated/empty "").
    btc_seed = snapshot.positions[0][1]
    assert btc_seed > 0, (
        f"seeded BTC position={btc_seed!r} — expected ~1.0 from walletBalance; "
        "availableToWithdraw is deprecated and returns '' for UNIFIED accounts"
    )

    # --- Step 4: fetch live market price and compute a resting limit price --------------------
    ticker = get_public_json(cfg.rest_base_url, "/v5/market/tickers",
                             {"category": "spot", "symbol": "BTCUSDT"})
    market_price = float(ticker["result"]["list"][0]["lastPrice"])

    tick_size = filters["tick_size"] or 0.01
    step_size = filters["step_size"] or 0.000001
    min_qty = filters["min_qty"] or 0.0001
    min_notional = filters["min_notional"] or 5.0

    tick_d = Decimal(str(tick_size))
    price = float((Decimal(str(market_price * 0.90)) / tick_d).to_integral_value(ROUND_DOWN) * tick_d)
    step_d = Decimal(str(step_size))
    raw_qty = max(min_qty, (min_notional * 2) / price)
    qty = float((Decimal(str(raw_qty)) / step_d).to_integral_value(ROUND_DOWN) * step_d)
    if qty < min_qty:
        qty = float((Decimal(str(min_qty)) / step_d).to_integral_value(ROUND_DOWN) * step_d)

    # --- Step 4 (cont.): submit a tiny BUY LIMIT far below market (should rest, not fill) ----
    coid = CoidMinter().mint()
    request = OrderRequest(client_order_id=coid, venue="bybit", symbol="BTCUSDT",
                           side=+1, qty=qty, order_type="limit", price=price)
    seen.clear()
    client.submit(request)

    event_types = {type(e).__name__ for e in seen}
    assert "OrderSubmitted" in event_types, f"OrderSubmitted missing: {event_types}"
    rejected = [e for e in seen if isinstance(e, OrderRejected)]
    if rejected:
        pytest.skip(f"demo rejected the test order: coid={coid!r} qty={qty} price={price} "
                    f"reason={rejected[0].reason!r}")

    accepted = [e for e in seen if isinstance(e, OrderAccepted)]
    assert accepted, "OrderAccepted not found"
    assert accepted[0].venue_order_id, f"empty venue_order_id: {accepted[0]!r}"

    # --- Step 5: cancel the resting order ------------------------------------------------------
    # BybitApiError retCode 110001 / 170213 (order-not-found) is swallowed by cancel() — safe.
    client.cancel(coid)

    # --- Step 6: re-reconcile — open orders for BTCUSDT should NOT include our coid -----------
    snapshot2 = client.connect()
    open_coids = {mo.client_order_id for mo in snapshot2.open_orders}
    assert coid not in open_coids, (
        f"coid {coid!r} still open after cancel — cancel may have failed silently. "
        f"open_coids={open_coids}"
    )
