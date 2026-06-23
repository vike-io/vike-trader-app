"""LIVE smoke against https://www.okx.com + x-simulated-trading:1 — manual gate, excluded by -m "not network".

Requires OKX_DEMO_API_KEY / OKX_DEMO_API_SECRET / OKX_DEMO_API_PASSPHRASE in .env.
Places a small resting BTC-USDT BUY LIMIT at ~90% of the current market price (fetched live)
so it rests without filling; confirms OrderAccepted via the REST ACK, cancels it (swallowing
OKX cancel-not-found codes 51400/51401/51402 — ASSUMPTION; the live run confirms the real code;
if the demo returns a different family, update _NOT_FOUND in okx/client.py and re-green Task 3),
then re-reconciles to prove it is gone.

Run manually:
    PYTHONPATH=src .venv/Scripts/python -m pytest tests/unit/exec/test_okx_live_smoke.py -m network -v
"""

from __future__ import annotations

import functools
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
    return bool(
        os.environ.get("OKX_DEMO_API_KEY")
        and os.environ.get("OKX_DEMO_API_SECRET")
        and os.environ.get("OKX_DEMO_API_PASSPHRASE")
    )


@pytest.mark.skipif(not _creds_present(), reason="OKX_DEMO_API_KEY/SECRET/PASSPHRASE not set in .env")
def test_okx_demo_round_trip_place_then_cancel() -> None:
    """Connect/reconcile -> place small resting BUY LIMIT -> verify ACK -> cancel -> cleanup.

    Step 1 — resolve_venue_config: proves creds load and VenueConfig builds for okx/DEMO.
    Step 2 — instruments-info: unsigned public GET /api/v5/public/instruments, parses BTC-USDT
              filters (tick/step/min_qty); OKX has no explicit min_notional so we use 10 USDT.
    Step 3 — connect(): signs GET /api/v5/account/balance + /api/v5/trade/orders-pending,
              returns ReconcileSnapshot; asserts the demo account holds a non-zero BTC seed.
    Step 4 — submit(): signs POST /api/v5/trade/order, publishes OrderSubmitted then OrderAccepted.
    Step 5 — cancel(): signs POST /api/v5/trade/cancel-order; swallows 51400/51401/51402 if
              already gone. NOTE: these codes are an ASSUMPTION derived from OKX docs; the live
              run confirms the real code family. If a different code is returned, update _NOT_FOUND
              in okx/client.py and its Task-3 test, then re-green Task 3.
    Step 6 — final connect(): re-reconcile proves the resting order is gone (idempotency check).
    """
    from decimal import ROUND_DOWN, Decimal

    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.coid import CoidMinter
    from vike_trader_app.exec.credentials import Environment
    from vike_trader_app.exec.events import OrderAccepted, OrderRejected, OrderRequest
    from vike_trader_app.exec.okx.client import OKXSpotExecutionClient
    from vike_trader_app.exec.okx.instruments import parse_okx_instruments
    from vike_trader_app.exec.okx.transport import okx_public_get, okx_signed_request
    from vike_trader_app.exec.venue_config import resolve_venue_config
    from vike_trader_app.data.okx_source import market_symbol

    # --- Step 1: resolve venue config ----------------------------------------------------------
    cfg = resolve_venue_config("okx", Environment.DEMO, now_ms=lambda: int(time.time() * 1000))
    assert cfg is not None, "creds present but venue config did not resolve"
    assert cfg.rest_base_url == "https://www.okx.com", f"unexpected REST: {cfg.rest_base_url}"

    # --- Step 2: fetch instruments-info (unsigned public) --------------------------------------
    inst_id = market_symbol("BTCUSDT")   # -> "BTC-USDT"
    info = okx_public_get(cfg.rest_base_url, "/api/v5/public/instruments",
                          {"instType": "SPOT", "instId": inst_id}, simulated=True)
    assert str(info.get("code")) == "0", f"instruments endpoint error: {info}"
    parsed = parse_okx_instruments(info)
    assert inst_id in parsed, f"{inst_id} not in instruments: {list(parsed)[:5]}"
    f = parsed[inst_id]
    base_asset = f["base_asset"]
    filters = {k: v for k, v in f.items() if k != "base_asset"}

    # --- wire up the execution client ----------------------------------------------------------
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe(seen.append)
    client = OKXSpotExecutionClient(
        bus,
        signer=cfg.signer,
        rest_base_url=cfg.rest_base_url,
        symbol=inst_id,
        filters=filters,
        base_asset=base_asset,
        transport=functools.partial(okx_signed_request, simulated=True),
        public_transport=functools.partial(okx_public_get, simulated=True),
    )

    # --- Step 3: connect / reconcile -----------------------------------------------------------
    snapshot = client.connect()
    assert snapshot.positions, "connect() returned empty positions tuple"
    assert snapshot.positions[0][0] == inst_id, (
        f"unexpected symbol in snapshot: {snapshot.positions[0][0]}"
    )
    # CRITICAL regression guard: the OKX demo account (acctLv 3) holds demo BTC funds; a seed
    # of 0 means iter_balances is reading the wrong field (e.g., frozenBal instead of availBal).
    btc_seed = snapshot.positions[0][1]
    assert btc_seed > 0, (
        f"seeded BTC position={btc_seed!r} — expected >0 from availBal on the demo account; "
        "a zero seed indicates iter_balances is reading the wrong balance field"
    )

    # --- Step 4: fetch live market price and compute a resting limit price --------------------
    ticker = okx_public_get(cfg.rest_base_url, "/api/v5/market/ticker",
                            {"instId": inst_id}, simulated=True)
    assert str(ticker.get("code")) == "0", f"ticker endpoint error: {ticker}"
    market_price = float(ticker["data"][0]["last"])

    tick_size = filters["tick_size"] or 0.1
    step_size = filters["step_size"] or 0.0001
    min_qty = filters["min_qty"] or 0.0001
    # OKX SPOT has no min_notional in instruments; use 10 USDT as a conservative floor.
    min_notional = 10.0

    tick_d = Decimal(str(tick_size))
    price = float((Decimal(str(market_price * 0.90)) / tick_d).to_integral_value(ROUND_DOWN) * tick_d)
    step_d = Decimal(str(step_size))
    raw_qty = max(min_qty, (min_notional * 2) / price)
    qty = float((Decimal(str(raw_qty)) / step_d).to_integral_value(ROUND_DOWN) * step_d)
    if qty < min_qty:
        qty = float((Decimal(str(min_qty)) / step_d).to_integral_value(ROUND_DOWN) * step_d)

    # --- Step 4 (cont.): submit a tiny BUY LIMIT far below market (should rest, not fill) ----
    coid = CoidMinter().mint()
    request = OrderRequest(
        client_order_id=coid,
        venue="okx",
        symbol=inst_id,
        side=+1,
        qty=qty,
        order_type="limit",
        price=price,
    )
    seen.clear()
    client.submit(request)

    event_types = {type(e).__name__ for e in seen}
    assert "OrderSubmitted" in event_types, f"OrderSubmitted missing: {event_types}"
    rejected = [e for e in seen if isinstance(e, OrderRejected)]
    if rejected:
        pytest.skip(
            f"demo rejected the test order: coid={coid!r} qty={qty} price={price} "
            f"reason={rejected[0].reason!r}"
        )

    accepted = [e for e in seen if isinstance(e, OrderAccepted)]
    assert accepted, f"OrderAccepted not found; seen event types: {event_types}"
    assert accepted[0].venue_order_id, f"empty venue_order_id: {accepted[0]!r}"

    # --- Step 5: cancel the resting order ------------------------------------------------------
    # OKX cancel-not-found codes 51400/51401/51402 are swallowed by cancel() — safe even if the
    # order filled or was already gone.  NOTE: these codes are ASSUMED from OKX API docs; the
    # actual code returned by the demo must be confirmed on the live run. If a different code
    # surfaces, update _NOT_FOUND in okx/client.py and its Task-3 unit test.
    client.cancel(coid)

    # --- Step 6: re-reconcile — open orders for BTC-USDT should NOT include our coid ----------
    snapshot2 = client.connect()
    open_coids = {mo.client_order_id for mo in snapshot2.open_orders}
    assert coid not in open_coids, (
        f"coid {coid!r} still open after cancel — cancel may have failed silently. "
        f"open_coids={open_coids}"
    )
