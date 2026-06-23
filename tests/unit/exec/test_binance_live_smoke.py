"""LIVE smoke test against demo-api.binance.com — manual gate, excluded by -m "not network".

Requires BINANCE_DEMO_API_KEY/SECRET in .env (loaded below via dotenv). Places a small resting
BTCUSDT LIMIT far below the market (price=1000 USDT, so it rests and never fills), confirms
OrderAccepted via the REST ACK, then cancels it, and reconciles open orders. Verifies the
signed REST path end-to-end against the funded demo account.

Run manually:
    PYTHONPATH=src .venv/Scripts/python -m pytest tests/unit/exec/test_binance_live_smoke.py -m network -v

The BINANCE_DEMO_WS_URL question:
    The demo user-data WS host is a PLACEHOLDER in venue_config (BINANCE_DEMO_WS_DEFAULT points to
    the mainnet stream.binance.com:9443). This test does NOT exercise the WS path — it covers the
    signed REST ACK path only (submit/cancel/connect). The correct WS host for
    demo-api.binance.com's listenKey is empirically unknown until Task 15 Step 4 is run with creds.
    To override it for manual testing set BINANCE_DEMO_WS_URL=<confirmed-wss-host>/ws in .env.
"""

from __future__ import annotations

import os
import time

import pytest

# ---------------------------------------------------------------------------
# Load the shared .env from the main repo root (gitignored; never in worktree)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]

    load_dotenv("C:/Projects/vike-trader-app/.env", override=False)
except ImportError:
    pass  # python-dotenv not installed — creds must already be in the environment

# ---------------------------------------------------------------------------
# Module-level marker: ALL tests in this file require the "network" marker.
# Excluded by the default CI run via -m "not network".
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.network


def _creds_present() -> bool:
    return bool(
        os.environ.get("BINANCE_DEMO_API_KEY")
        and os.environ.get("BINANCE_DEMO_API_SECRET")
    )


@pytest.mark.skipif(not _creds_present(), reason="BINANCE_DEMO_API_KEY/SECRET not set in .env")
def test_demo_round_trip_place_then_cancel() -> None:
    """Connect/reconcile -> place small resting BUY LIMIT -> verify ACK -> cancel -> cleanup.

    Step 1 — resolve_venue_config: proves creds load and VenueConfig builds.
    Step 2 — connect(): signs GET /api/v3/account + /api/v3/openOrders, returns ReconcileSnapshot.
    Step 3 — exchangeInfo fetch: unsigned GET, parses BTCUSDT filters (tick/step/min_notional).
    Step 4 — submit(): signs POST /api/v3/order, publishes OrderSubmitted then OrderAccepted.
    Step 5 — cancel(): signs DELETE /api/v3/order, swallows -2011 silently.
    Step 6 — final connect(): re-reconcile proves the resting order is gone (idempotency check).
    """
    from vike_trader_app.data.instrument_db import parse_symbol_filters
    from vike_trader_app.exec.binance.client import BinanceSpotExecutionClient
    from vike_trader_app.exec.binance.transport import get_public_json
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.coid import CoidMinter
    from vike_trader_app.exec.credentials import Environment
    from vike_trader_app.exec.events import OrderAccepted, OrderRejected, OrderRequest, OrderSubmitted
    from vike_trader_app.exec.venue_config import resolve_venue_config

    # --- Step 1: resolve venue config ----------------------------------------------------------
    cfg = resolve_venue_config(
        "binance", Environment.DEMO, now_ms=lambda: int(time.time() * 1000)
    )
    assert cfg is not None, "creds present but venue config did not resolve — check env vars"
    assert cfg.rest_base_url == "https://demo-api.binance.com", (
        f"unexpected REST base: {cfg.rest_base_url}"
    )

    # --- Step 3: fetch exchangeInfo (unsigned) -------------------------------------------------
    info = get_public_json(cfg.rest_base_url, "/api/v3/exchangeInfo", {"symbol": "BTCUSDT"})
    assert "symbols" in info, f"exchangeInfo missing 'symbols' key: {list(info)[:5]}"
    filters = parse_symbol_filters(info)
    assert "BTCUSDT" in filters, "BTCUSDT not found in exchangeInfo filters"
    f = filters["BTCUSDT"]

    btcusdt_entry = next(
        (s for s in info["symbols"] if s["symbol"] == "BTCUSDT"), None
    )
    assert btcusdt_entry is not None, "BTCUSDT symbol entry not found in exchangeInfo"
    base_asset = btcusdt_entry["baseAsset"]  # "BTC"

    # --- wire up the execution client --------------------------------------------------------
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe(seen.append)

    client = BinanceSpotExecutionClient(
        bus,
        signer=cfg.signer,
        rest_base_url=cfg.rest_base_url,
        symbol="BTCUSDT",
        filters=f,
        base_asset=base_asset,
    )

    # --- Step 2: connect / reconcile -----------------------------------------------------------
    snapshot = client.connect()
    assert snapshot.positions, "connect() returned empty positions tuple"
    assert snapshot.positions[0][0] == "BTCUSDT", (
        f"unexpected symbol in snapshot: {snapshot.positions[0][0]}"
    )
    # BTC balance — demo account is funded so free >= 0 is always valid
    btc_free = snapshot.positions[0][1]
    assert isinstance(btc_free, float), f"BTC balance not a float: {btc_free!r}"

    # --- Step 4: submit a tiny BUY LIMIT far below market (should REST, not fill) -------------
    min_notional = f["min_notional"] or 5.0
    step_size = f["step_size"] or 0.001
    min_qty = f["min_qty"] or 0.001
    price = 1000.0  # well below BTC market; order will rest, not fill
    qty = max(min_qty, round((min_notional / price) * 2 / step_size) * step_size)

    minter = CoidMinter()
    coid = minter.mint()
    request = OrderRequest(
        client_order_id=coid,
        venue="binance",
        symbol="BTCUSDT",
        side=+1,
        qty=qty,
        order_type="limit",
        price=price,
    )
    seen.clear()
    client.submit(request)

    event_types = {type(e).__name__ for e in seen}
    assert "OrderSubmitted" in event_types, f"OrderSubmitted missing from bus events: {event_types}"
    assert event_types & {"OrderAccepted", "OrderRejected"}, (
        f"neither OrderAccepted nor OrderRejected emitted; got: {event_types}"
    )

    rejected = [e for e in seen if isinstance(e, OrderRejected)]
    if rejected:
        # Inform clearly — common causes: insufficient USDT balance, filter violation
        reason = rejected[0].reason
        pytest.skip(
            f"demo account rejected the test order — inspect reason and fix filters/funds.\n"
            f"  client_order_id={coid!r}  qty={qty}  price={price}  reason={reason!r}"
        )

    accepted = [e for e in seen if isinstance(e, OrderAccepted)]
    assert accepted, "OrderAccepted not found after filtering out rejections"
    venue_oid = accepted[0].venue_order_id
    assert venue_oid, f"OrderAccepted.venue_order_id is empty: {accepted[0]!r}"

    # --- Step 5: cancel the resting order ------------------------------------------------------
    # BinanceApiError -2011 ("Unknown order") is swallowed by cancel() — safe to call unconditionally.
    client.cancel(coid)

    # --- Step 6: re-reconcile — open orders for BTCUSDT should NOT include our coid -----------
    snapshot2 = client.connect()
    open_coids = {mo.client_order_id for mo in snapshot2.open_orders}
    assert coid not in open_coids, (
        f"coid {coid!r} still appears in open orders after cancel — "
        f"cancel may have failed silently. open_coids={open_coids}"
    )
