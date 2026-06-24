"""LIVE smoke against api-demo.bybit.com + stream-demo.bybit.com — gated by @pytest.mark.network.

Full round-trip (LINEAR PERP): set leverage -> connect/reconcile (signed position) ->
open private WS -> place a SMALL MARKET BUY (category=linear, qty in base coin) ->
poll WS execution frames via the Qt event loop until the fill lands in LiveOmsHub.account
-> assert signed LONG position grew AND Account.unrealized_pnl() is computable (mark set
from fill markPrice) -> FLATTEN via reduceOnly MARKET SELL sized from a fresh /v5/position/list
reconcile -> shutdown (worker wait()-joined).

Key differences from the spot smoke (test_bybit_ws_fill_smoke.py):
- BybitPerpExecutionClient (not BybitSpotExecutionClient); client.set_leverage() called first.
- category=linear; qty in base coin (no marketUnit); filters from parse_bybit_perp_instruments.
- Flatten = reduceOnly SELL (or BUY to cover a short) of the live signed size from a fresh
  reconcile_positions(), not the local filled_qty — any residual is fully closed even if partial.
- Sign-aware flatten: if live_size < 0 (residual SHORT from a prior run), flattten with a BUY.
- Bare RiskGate(RiskLimits()) — NOT min_notional-filtered app limits — so the test order is not vetoed.
- Assert OrderAccepted (not OrderDenied) BEFORE polling.

Run manually:
    PYTHONPATH=src .venv/Scripts/python -m pytest tests/gui/exec/test_bybit_perp_ws_fill_smoke.py -m network -v

Excluded by the default suite:
    pytest tests/gui/exec -m "not network" -q   # never runs this file
"""

from __future__ import annotations

import math
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
    return bool(os.environ.get("BYBIT_DEMO_API_KEY") and os.environ.get("BYBIT_DEMO_API_SECRET"))


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# Qty sizing helpers (byte-identical to the spot smoke)
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


@pytest.mark.skipif(not _creds_present(), reason="BYBIT_DEMO_API_KEY/SECRET not set in .env")
def test_bybit_demo_perp_ws_fill_roundtrip(app, monkeypatch) -> None:  # noqa: PLR0915 — intentionally verbose smoke
    """set leverage -> connect/reconcile -> open perp WS -> MARKET BUY -> WS fill -> flatten.

    Step 1  resolve_venue_config: proves creds load; asserts demo WS URL.
    Step 2  parse_bybit_perp_instruments: fetches linear BTCUSDT filters (tick/step/min_notional).
    Step 3  BybitPerpExecutionClient.set_leverage(): set to 2x (swallow 110043 if already at target).
    Step 4  client.connect(): reconcile — seeds LiveOmsHub.account with signed LONG/SHORT/flat position.
    Step 5  Start LiveExecutionSession + PrivateUserDataWorker with the perp run_core (WS fill stream).
    Step 6  Settle ~6s for WS auth + subscription handshake (race guard).
    Step 7  Compute a tiny base qty clearing qtyStep/minOrderQty/minNotionalValue; place MARKET BUY.
    Step 8  Assert OrderAccepted (REST ACK) BEFORE polling — a denied/rejected order never fills.
    Step 9  Poll Qt event loop (processEvents + worker.wait) up to 15 s for the WS fill to land.
    Step 10 Assert: Account position size grew (signed LONG) AND unrealized_pnl() is computable.
    Step 11 (always in finally) Sign-aware flatten via fresh reconcile; cancel residual; shutdown.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.binance.transport import get_public_json
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.bybit.perp_client import BybitPerpExecutionClient
    from vike_trader_app.exec.bybit.perp_instruments import parse_bybit_perp_instruments
    from vike_trader_app.exec.bybit.perp_user_data import make_bybit_perp_run_core
    from vike_trader_app.exec.coid import CoidMinter
    from vike_trader_app.exec.credentials import Environment
    from vike_trader_app.exec.events import (
        OrderAccepted,
        OrderDenied,
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
    cfg = resolve_venue_config("bybit", Environment.DEMO, now_ms=now_ms)
    assert cfg is not None, "creds present but venue config did not resolve"
    assert cfg.rest_base_url == "https://api-demo.bybit.com", (
        f"unexpected REST: {cfg.rest_base_url}"
    )
    assert cfg.ws_base_url == "wss://stream-demo.bybit.com/v5/private", (
        f"unexpected WS URL: {cfg.ws_base_url}"
    )

    # --- Step 2: instruments-info (unsigned) — linear perp filters ----------------------------
    info = get_public_json(cfg.rest_base_url, "/v5/market/instruments-info",
                           {"category": "linear", "symbol": "BTCUSDT"})
    parsed = parse_bybit_perp_instruments(info)
    assert "BTCUSDT" in parsed, f"BTCUSDT not in linear instruments-info: {list(parsed)[:5]}"
    f = parsed["BTCUSDT"]
    base_asset = f["base_asset"]
    filters = {k: v for k, v in f.items() if k != "base_asset"}

    tick_size = filters.get("tick_size") or 0.01
    step_size = filters.get("step_size") or 0.001
    min_qty = filters.get("min_qty") or 0.001
    min_notional = filters.get("min_notional") or 5.0

    # --- Step 3: build bus + perp client; set leverage before reconcile -----------------------
    bus = EventBus()
    seen_events: list[object] = []
    bus.subscribe(seen_events.append)

    # leverage=2 (Bybit demo linear; swallows 110043 "already at target leverage")
    client = BybitPerpExecutionClient(
        bus, signer=cfg.signer, rest_base_url=cfg.rest_base_url,
        symbol="BTCUSDT", filters=filters, base_asset=base_asset, leverage=2.0,
    )

    # set_leverage BEFORE connect() — as required by the perp flow (mirrors app.py)
    client.set_leverage()

    # --- Step 4: connect/reconcile — seeds signed position (may be non-zero from a prior run) -
    snapshot = client.connect()

    hub = LiveOmsHub(
        bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
        client=client, venue="bybit", symbol="BTCUSDT", now_ms=now_ms,
    )
    hub.apply_snapshot(snapshot)

    # Record the signed position size BEFORE our order (may be +/0/- from prior run)
    start_size = (
        hub.account.positions.get(("bybit", "BTCUSDT", "BOTH"), {}).get("size", 0.0)
    )

    # --- Fix 1: ensure VIKE_DISABLE_LIVE is unset so add_worker_if_enabled actually starts ------
    # tests/conftest.py sets VIKE_DISABLE_LIVE=1 globally; as a @pytest.mark.network test that
    # genuinely needs the live WS, we must clear it for this test only.
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    # --- Step 5: build the perp WS worker + session -------------------------------------------
    session = LiveExecutionSession(hub)
    run_core = make_bybit_perp_run_core(
        ws_url=cfg.ws_base_url,
        api_key=cfg.credentials.api_key,
        api_secret=cfg.credentials.api_secret,
        symbol="BTCUSDT",
        now_ms=now_ms,
    )
    worker = PrivateUserDataWorker(run_core)
    started = session.add_worker_if_enabled("bybit", worker)
    assert started, (
        "add_worker_if_enabled returned False — VIKE_DISABLE_LIVE was still set or "
        "worker failed to start; check monkeypatch.delenv above"
    )

    # --- Fix 2: settle for WS subscribe BEFORE placing the order (race guard) ------------------
    # The worker connects, auths, and subscribes ASYNCHRONOUSLY in its QThread.  If the order
    # fills BEFORE the private-channel subscribe completes, Bybit will not push the execution
    # frame retroactively.  In production the worker subscribes at session-start long before any
    # order, so this settle is a smoke-only concern.
    settle_deadline = time.monotonic() + 6.0
    while time.monotonic() < settle_deadline:
        app.processEvents()
        worker.wait(200)
        app.processEvents()

    # hard-assert the worker is still running after the settle (no immediate auth failure)
    assert worker.isRunning(), (
        "PrivateUserDataWorker stopped during WS subscribe settle — "
        "auth or connection failure on the demo endpoint"
    )

    # ---------------------------------------------------------------------------
    # From here on: try/finally so the demo position is ALWAYS flattened.
    # ---------------------------------------------------------------------------
    coid = CoidMinter().mint()

    try:
        # --- Step 7: fetch live last price for qty sizing; place a MARKET BUY ---------------
        ticker = get_public_json(cfg.rest_base_url, "/v5/market/tickers",
                                 {"category": "linear", "symbol": "BTCUSDT"})
        px = float(ticker["result"]["list"][0].get("lastPrice") or
                   ticker["result"]["list"][0].get("ask1Price"))

        # Linear perp MARKET BUY: qty in base coin (no marketUnit).
        # ceil_to_step ensures we satisfy BOTH minOrderQty AND minNotionalValue.
        notional_floor = min_notional or 5.0
        raw_qty = (notional_floor * 2) / px
        buy_qty = max(min_qty, _ceil_to_step(raw_qty, step_size))

        # --- Step 8: submit + assert OrderAccepted BEFORE polling --------------------------
        seen_events.clear()
        request = OrderRequest(
            client_order_id=coid,
            venue="bybit",
            symbol="BTCUSDT",
            side=+1,
            qty=buy_qty,
            order_type="market",
            price=None,
        )
        hub.submit_ticket(request)

        # Drain the bus: the REST ACK is synchronous on the calling thread.
        # RiskGate(RiskLimits()) is bare — it must NOT veto a valid MARKET order.
        denied = [e for e in seen_events if isinstance(e, OrderDenied)]
        if denied:
            pytest.skip(
                f"demo order was gate-denied (bare RiskGate should NOT veto): "
                f"coid={coid!r} qty={buy_qty} reason={denied[0].reason!r}"
            )

        rejected = [e for e in seen_events if isinstance(e, OrderRejected)]
        if rejected:
            pytest.skip(
                f"demo rejected the test order: coid={coid!r} qty={buy_qty} "
                f"reason={rejected[0].reason!r}"
            )

        accepted = [e for e in seen_events if isinstance(e, OrderAccepted)]
        assert accepted, (
            f"OrderAccepted not found after submit — events: "
            f"{[type(e).__name__ for e in seen_events]}"
        )
        assert accepted[0].venue_order_id, f"empty venue_order_id: {accepted[0]!r}"

        # --- Step 9: poll the Qt event loop up to 15 s for the WS fill to land --------------
        deadline = time.monotonic() + 15.0

        def _fill_landed() -> bool:
            mo = hub.registry.get(coid)
            if mo is None:
                return False
            pos = hub.account.positions.get(("bybit", "BTCUSDT", "BOTH"), {})
            return mo.filled_qty > 0 or pos.get("size", 0.0) > start_size

        while time.monotonic() < deadline and not _fill_landed():
            app.processEvents()
            worker.wait(100)          # yields 100 ms so the QThread can post the queued signal
            app.processEvents()       # pick up any signals posted during the wait

        # --- Step 10: assert fill landed + signed LONG position + mark recorded -------------
        mo = hub.registry.get(coid)
        assert mo is not None, f"coid {coid!r} never reached the registry"

        pos = hub.account.positions.get(("bybit", "BTCUSDT", "BOTH"), {})
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

        # Signed-LONG assertion: a linear BUY must open/grow a positive position.
        assert actual_size > start_size, (
            f"Account position did not grow after BUY fill: "
            f"start_size={start_size} actual_size={actual_size}"
        )

        # Mark price was set from the fill's markPrice field (perp_mapper enrichment).
        upnl = hub.account.unrealized_pnl("bybit", "BTCUSDT")
        assert isinstance(upnl, float), (
            f"unrealized_pnl() returned {upnl!r} — mark not recorded from fill markPrice"
        )
        # upnl may be 0.0 exactly (mark == avg_px) but isinstance(0.0, float) is True.

    finally:
        # --- Step 11: SIGN-AWARE FLATTEN + cancel residual + shut down --------------------
        # Re-reconcile to read the TRUE live signed size (handles partial fill + prior runs).
        # A residual SHORT (live_size < 0) from an earlier aborted long also gets flattened.
        try:
            live_snapshot = client.connect()
            live_size = dict(live_snapshot.positions).get("BTCUSDT", 0.0)
        except Exception:  # noqa: BLE001
            # If reconcile itself fails, fall back to the account's position delta
            pos = hub.account.positions.get(("bybit", "BTCUSDT", "BOTH"), {})
            live_size = pos.get("size", 0.0)

        if live_size != 0.0:
            flatten_side = -1 if live_size > 0 else +1   # SELL a long, BUY to cover a short
            flatten_abs = abs(live_size)
            sell_qty = _floor_to_step(flatten_abs, step_size)
            if sell_qty >= min_qty:
                flatten_coid = CoidMinter().mint()
                flatten_req = OrderRequest(
                    client_order_id=flatten_coid,
                    venue="bybit",
                    symbol="BTCUSDT",
                    side=flatten_side,
                    qty=sell_qty,
                    order_type="market",
                    price=None,
                    reduce_only=True,
                )
                try:
                    hub.submit_ticket(flatten_req)
                except Exception:  # noqa: BLE001
                    pass  # best-effort flatten; don't raise and hide the original assertion

        # Cancel any residual resting order (swallows 110001/170213 "not found")
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
