"""LIVE smoke against demo-api.binance.com + demo-ws-api.binance.com — gated by @pytest.mark.network.

Full round-trip: connect/reconcile -> open WS-API fill stream -> place a SMALL MARKET BUY
-> poll WS executionReport frames via the Qt event loop until the fill lands in
LiveOmsHub.account -> assert position moved -> FLATTEN and clean up.

Binance-specific adaptations vs the Bybit/OKX smokes:
  - Plain symbol "BTCUSDT" EVERYWHERE (no dashes, no instId).
  - MARKET BUY sized by quantity (not quoteOrderQty) — matches BinanceSpotExecutionClient.build_order_params.
  - No passphrase — Binance uses HMAC only.
  - Creds: BINANCE_DEMO_API_KEY / BINANCE_DEMO_API_SECRET.
  - demo REST host: demo-api.binance.com; WS host: demo-ws-api.binance.com/ws-api/v3.
  - Filter parse via parse_symbol_filters (instrument_db) + get_public_json (/api/v3/exchangeInfo).
  - Binance WS-API auto-pongs 20-second PING control frames (no app-level ping); pong checkpoint
    asserts no failed signal fired during the ~6-s subscribe settle.
  - cancel() swallows -2011 ("Unknown order") via is_order_not_found() in client.py:93.

This test GENUINELY FILLS a demo order and ALWAYS cleans up via try/finally.

Run manually:
    PYTHONPATH=src .venv/Scripts/python -m pytest tests/gui/exec/test_binance_ws_fill_smoke.py -m network -v

Excluded by the default suite:
    pytest tests/gui/exec -m "not network" -q   # never runs this file
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.network

# ---------------------------------------------------------------------------
# Creds guard
# ---------------------------------------------------------------------------

def _creds_present() -> bool:
    return bool(
        os.environ.get("BINANCE_DEMO_API_KEY")
        and os.environ.get("BINANCE_DEMO_API_SECRET")
    )


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# Qty sizing helpers (same pattern as the Bybit/OKX smokes)
# ---------------------------------------------------------------------------

def _ceil_to_step(value: float, step: float) -> float:
    """Round *up* to the nearest step multiple (so we always meet min_notional)."""
    from decimal import ROUND_UP, Decimal
    step_d = Decimal(str(step))
    return float((Decimal(str(value)) / step_d).to_integral_value(ROUND_UP) * step_d)


def _floor_to_step(value: float, step: float) -> float:
    from decimal import ROUND_DOWN, Decimal
    step_d = Decimal(str(step))
    return float((Decimal(str(value)) / step_d).to_integral_value(ROUND_DOWN) * step_d)


def _floor_to_tick(value: float, tick: float) -> float:
    from decimal import ROUND_DOWN, Decimal
    tick_d = Decimal(str(tick))
    return float((Decimal(str(value)) / tick_d).to_integral_value(ROUND_DOWN) * tick_d)


# ---------------------------------------------------------------------------
# The smoke test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _creds_present(), reason="BINANCE_DEMO_API_KEY/SECRET not set in .env")
def test_binance_demo_ws_fill_roundtrip(app, monkeypatch) -> None:  # noqa: PLR0915 — intentionally verbose smoke
    """Connect -> open WS-API fill stream -> marketable BUY -> WS fill -> Account reflects it -> flatten.

    Step 1  resolve_venue_config: proves creds load; asserts demo REST + WS URLs.
    Step 2  exchangeInfo: parse BTCUSDT filters (tick/step/min_qty/min_notional/base_asset).
    Step 3  connect(): reconcile — seeds LiveOmsHub.account from /api/v3/account + openOrders.
    Step 4  Start LiveExecutionSession + PrivateUserDataWorker (the WS-API fill stream).
            Settle ~6s (processEvents + worker.wait) so the signed-subscribe round-trip completes.
            Pong checkpoint: assert NO failed signal fired AND worker.isRunning()
            (Binance WS-API auto-pongs protocol PING frames — no app-level ping needed).
    Step 5  Fetch live price (/api/v3/ticker/price); size a step-aligned MARKET BUY qty that
            clears MIN_NOTIONAL + LOT_SIZE filters (notional_floor=$10 default).
    Step 6  Submit OrderRequest(side=+1, qty=buy_qty, order_type="market", price=None);
            drain bus; skip on OrderRejected; assert OrderAccepted with non-empty venue_order_id.
    Step 7  Poll Qt event loop (processEvents + worker.wait) up to 15 s for the WS fill.
    Step 8  Assert: registry filled_qty>0 AND status in {FILLED, PARTIALLY_FILLED}.
    Step 9  (always in finally) Flatten via MARKET SELL of filled qty; cancel residual
            (client.cancel swallows -2011); session.shutdown(); assert not worker.isRunning().
    """
    pytest.importorskip("PySide6")

    from vike_trader_app.data.instrument_db import parse_symbol_filters
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.binance.client import BinanceSpotExecutionClient
    from vike_trader_app.exec.binance.transport import get_public_json
    from vike_trader_app.exec.binance.user_data import make_binance_run_core
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.coid import CoidMinter
    from vike_trader_app.exec.credentials import Environment
    from vike_trader_app.exec.events import (
        OrderAccepted,
        OrderRejected,
        OrderRequest,
    )
    from vike_trader_app.exec.live_oms import LiveOmsHub
    from vike_trader_app.exec.order import OrderStatus
    from vike_trader_app.exec.risk import RiskGate, RiskLimits
    from vike_trader_app.exec.venue_config import resolve_venue_config
    from vike_trader_app.ui.private_user_data import LiveExecutionSession, PrivateUserDataWorker

    # --- Step 1: resolve venue config ----------------------------------------------------------
    now_ms = lambda: int(time.time() * 1000)
    cfg = resolve_venue_config("binance", Environment.DEMO, now_ms=now_ms)
    assert cfg is not None, "creds present but venue config did not resolve — check env vars"
    assert cfg.rest_base_url == "https://demo-api.binance.com", (
        f"unexpected REST base: {cfg.rest_base_url}"
    )
    assert cfg.ws_base_url == "wss://demo-ws-api.binance.com/ws-api/v3", (
        f"unexpected WS URL: {cfg.ws_base_url}"
    )

    # --- Step 2: exchangeInfo (unsigned public) -------------------------------------------------
    # parse_symbol_filters extracts PRICE_FILTER.tickSize, LOT_SIZE.{stepSize,minQty}, NOTIONAL
    info = get_public_json(cfg.rest_base_url, "/api/v3/exchangeInfo", {"symbol": "BTCUSDT"})
    assert "symbols" in info, f"exchangeInfo missing 'symbols' key: {list(info)[:5]}"
    filters_map = parse_symbol_filters(info)
    assert "BTCUSDT" in filters_map, f"BTCUSDT not found in exchangeInfo filters: {list(filters_map)[:5]}"
    f = filters_map["BTCUSDT"]

    btcusdt_entry = next(
        (s for s in info["symbols"] if s["symbol"] == "BTCUSDT"), None
    )
    assert btcusdt_entry is not None, "BTCUSDT symbol entry not found in exchangeInfo"
    base_asset = btcusdt_entry["baseAsset"]  # "BTC"

    tick_size = f.get("tick_size") or 0.01
    step_size = f.get("step_size") or 0.00001
    min_qty = f.get("min_qty") or 0.00001
    min_notional = f.get("min_notional") or 0.0  # NOTIONAL filter on Binance spot

    # --- Step 3: build bus + client; connect/reconcile -----------------------------------------
    bus = EventBus()
    seen_events: list[object] = []
    bus.subscribe(seen_events.append)

    client = BinanceSpotExecutionClient(
        bus,
        signer=cfg.signer,
        rest_base_url=cfg.rest_base_url,
        symbol="BTCUSDT",
        filters=f,
        base_asset=base_asset,
    )

    snapshot = client.connect()
    assert snapshot.positions, "connect() returned empty positions"

    hub = LiveOmsHub(
        bus=bus, account=Account(venue="binance"), gate=RiskGate(RiskLimits()),
        client=client, venue="binance", symbol="BTCUSDT", now_ms=now_ms,
    )
    hub.apply_snapshot(snapshot)

    # Record starting position size (may already hold BTC from a prior run)
    start_size = (
        hub.account.positions.get(("binance", "BTCUSDT", "BOTH"), {}).get("size", 0.0)
    )

    # --- Fix 1: ensure VIKE_DISABLE_LIVE is unset so add_worker_if_enabled actually starts ------
    # tests/conftest.py sets VIKE_DISABLE_LIVE=1 globally; as a @pytest.mark.network test that
    # genuinely needs the live WS, we must clear it for this test only.
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    # --- Step 4: build the WS worker + session --------------------------------------------------
    # Capture failed signal emissions for the subscribe-settle checkpoint.
    failed_messages: list[str] = []

    session = LiveExecutionSession(hub)
    run_core = make_binance_run_core(
        ws_url=cfg.ws_base_url,
        api_key=cfg.credentials.api_key,
        api_secret=cfg.credentials.api_secret,
        symbol="BTCUSDT",
        now_ms=now_ms,
        # NO passphrase — Binance uses HMAC only, unlike OKX
    )
    worker = PrivateUserDataWorker(run_core)
    # Wire up failed-signal capture BEFORE add_worker so we don't miss early failures
    worker.failed.connect(failed_messages.append)

    started = session.add_worker_if_enabled("binance", worker)
    assert started, (
        "add_worker_if_enabled returned False — VIKE_DISABLE_LIVE was still set or "
        "worker failed to start; check monkeypatch.delenv above"
    )

    # --- Fix 2: settle for WS signed-subscribe BEFORE placing the order (race guard) -----------
    # The worker connects to demo-ws-api.binance.com and sends a per-request-signed
    # userDataStream.subscribe.signature request (HMAC; no session.logon) ASYNCHRONOUSLY in its
    # QThread.  If the order fills BEFORE the subscribe ACK is
    # received, the demo will not push the executionReport retroactively.  In production the
    # worker subscribes at session-start long before any order, so this settle is smoke-only.
    # PrivateUserDataWorker exposes no "subscribed" signal; pump the event loop for ~6 s —
    # enough for the demo WS to complete the signed-subscribe round-trip (handshake_timeout=10s).
    settle_deadline = time.monotonic() + 6.0
    while time.monotonic() < settle_deadline:
        app.processEvents()
        worker.wait(200)
        app.processEvents()

    # --- Subscribe-settle checkpoint: no crash from auto-pong / auth failure ------------------
    # Binance WS-API server sends 20-second WS protocol PING control frames; websockets
    # auto-pongs them at the protocol level (no app-level ping needed).  If auth broke or the
    # WS crashed during settle, the worker would emit failed() and/or stop.
    assert not failed_messages, (
        f"worker emitted failed() during settle — WS auth or subscribe broke: {failed_messages}"
    )
    assert worker.isRunning(), (
        "PrivateUserDataWorker stopped during 6-s settle — WS auth or subscribe crashed; "
        "check open_binance_user_data_ws handshake + build_subscribe_request signing"
    )

    # ---------------------------------------------------------------------------
    # From here on: try/finally so the demo position is ALWAYS flattened.
    # ---------------------------------------------------------------------------
    coid = CoidMinter().mint()
    flatten_coid: str | None = None
    filled_qty: float = 0.0

    try:
        # --- Step 5: fetch live price for qty sizing; compute a step-aligned MARKET BUY qty ----
        ticker = get_public_json(cfg.rest_base_url, "/api/v3/ticker/price", {"symbol": "BTCUSDT"})
        price = float(ticker.get("price", 0) or 0)
        assert price > 0, f"Could not fetch a live price from Binance demo: {ticker}"

        # Binance MARKET BUY is sized by quantity (build_order_params uses "quantity", not
        # "quoteOrderQty"), so compute a BASE qty large enough to clear MIN_NOTIONAL + LOT_SIZE.
        # notional_floor: MIN_NOTIONAL on Binance spot BTCUSDT is typically $5-10; use max(parsed,
        # 10.0) as the floor so the demo never rejects a sub-notional order.
        notional_floor = max(min_notional, 10.0)
        # ceil_to_step ensures the qty is an exact LOT_SIZE multiple and meets min_notional*1.5.
        raw_qty = max(min_qty, _ceil_to_step((notional_floor * 1.5) / price, step_size))
        buy_qty = raw_qty  # already step-aligned by _ceil_to_step

        # --- Step 6: submit + assert OrderAccepted BEFORE polling --------------------------
        seen_events.clear()
        request = OrderRequest(
            client_order_id=coid,
            venue="binance",
            symbol="BTCUSDT",
            side=+1,
            qty=buy_qty,
            order_type="market",
            price=None,
        )
        hub.submit_ticket(request)

        # Drain the bus: the REST ACK is synchronous on the calling thread.
        rejected = [e for e in seen_events if isinstance(e, OrderRejected)]
        if rejected:
            pytest.skip(
                f"demo rejected the test order: coid={coid!r} qty={buy_qty} "
                f"price={price} reason={rejected[0].reason!r}"
            )

        accepted = [e for e in seen_events if isinstance(e, OrderAccepted)]
        assert accepted, (
            f"OrderAccepted not found after submit — events: "
            f"{[type(e).__name__ for e in seen_events]}"
        )
        assert accepted[0].venue_order_id, f"empty venue_order_id: {accepted[0]!r}"

        # --- Step 7: poll the Qt event loop up to 15 s for the WS fill to land --------------
        deadline = time.monotonic() + 15.0

        def _fill_landed() -> bool:
            mo = hub.registry.get(coid)
            if mo is None:
                return False
            pos = hub.account.positions.get(("binance", "BTCUSDT", "BOTH"), {})
            return mo.filled_qty > 0 or pos.get("size", 0.0) > start_size

        while time.monotonic() < deadline and not _fill_landed():
            app.processEvents()
            worker.wait(100)          # yields 100 ms so the QThread can post the queued signal
            app.processEvents()       # pick up any signals posted during the wait

        # --- Step 8: assert fill landed -------------------------------------------------------
        mo = hub.registry.get(coid)
        assert mo is not None, f"coid {coid!r} never reached the registry"

        pos = hub.account.positions.get(("binance", "BTCUSDT", "BOTH"), {})
        actual_size = pos.get("size", 0.0)

        assert mo.filled_qty > 0 or actual_size > start_size, (
            f"WS fill did not arrive within 15 s: "
            f"filled_qty={mo.filled_qty} status={mo.status} "
            f"start_size={start_size} actual_size={actual_size}"
        )
        assert mo.status in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}, (
            f"unexpected order status after fill poll: {mo.status}"
        )
        assert mo.filled_qty > 0, f"filled_qty is 0 but status is {mo.status}"

        filled_qty = mo.filled_qty

    finally:
        # --- Step 9: FLATTEN + cancel residual + shut down --------------------------------
        # flatten_qty = what actually filled (tolerates partial fill or no fill at all)
        flatten_qty = filled_qty
        if flatten_qty == 0.0:
            # Fallback: check the account position delta directly.
            pos = hub.account.positions.get(("binance", "BTCUSDT", "BOTH"), {})
            flatten_qty = max(0.0, pos.get("size", 0.0) - start_size)

        if flatten_qty > 0.0:
            # Floor to step_size so the SELL qty is a valid LOT_SIZE multiple.
            # Binance MARKET SELL uses "quantity" (base units) — same code path as the BUY.
            sell_qty = _floor_to_step(flatten_qty, step_size)
            if sell_qty >= min_qty:
                flatten_coid = CoidMinter().mint()
                flatten_req = OrderRequest(
                    client_order_id=flatten_coid,
                    venue="binance",
                    symbol="BTCUSDT",
                    side=-1,
                    qty=sell_qty,
                    order_type="market",
                    price=None,
                )
                try:
                    hub.submit_ticket(flatten_req)
                except Exception:  # noqa: BLE001
                    pass  # best-effort flatten; don't raise and mask the original assertion

        # Cancel any residual resting BUY — BinanceSpotExecutionClient.cancel() already swallows
        # BinanceApiError -2011 ("Unknown order") via is_order_not_found() at client.py:93.
        try:
            client.cancel(coid)
        except Exception:  # noqa: BLE001
            pass

        # Tear down the WS worker + hub (joins the QThread — 0xC0000409 invariant).
        session.shutdown()
        assert not worker.isRunning(), (
            "PrivateUserDataWorker still running after session.shutdown() — "
            "teardown did not join the thread"
        )
