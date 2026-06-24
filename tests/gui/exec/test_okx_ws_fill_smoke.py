"""LIVE smoke against www.okx.com (x-simulated-trading:1) + wspap WS — gated by @pytest.mark.network.

Full round-trip: connect/reconcile -> open private WS -> place a SMALL MARKET BUY
-> poll WS execution frames via the Qt event loop until the fill lands in
LiveOmsHub.account -> assert position moved -> FLATTEN and clean up.

OKX specifics vs the Bybit smoke:
  - Dashed symbol "BTC-USDT" EVERYWHERE (inst_id form, not compact "BTCUSDT").
  - tgtCcy="base_ccy" on a MARKET BUY (client.py:79) — qty is in BTC not USDT.
  - min_notional=0.0 from parse_okx_instruments — use notional_floor=5.0 as the effective floor.
  - Passphrase required (OKX_DEMO_API_PASSPHRASE).
  - Pong checkpoint: after WS settle, assert worker still running (the Task-3 'pong' skip held).
  - Cancel swallows codes 51400/51401/51402 (the OKX not-found family).

This test GENUINELY FILLS a demo order and ALWAYS cleans up via try/finally.

Run manually:
    PYTHONPATH=src .venv/Scripts/python -m pytest tests/gui/exec/test_okx_ws_fill_smoke.py -m network -v

Excluded by the default suite:
    pytest tests/gui/exec -m "not network" -q   # never runs this file
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.network

# ---------------------------------------------------------------------------
# Creds guard
# ---------------------------------------------------------------------------

def _creds_present() -> bool:
    return bool(
        os.environ.get("OKX_DEMO_API_KEY")
        and os.environ.get("OKX_DEMO_API_SECRET")
        and os.environ.get("OKX_DEMO_API_PASSPHRASE")
    )


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# Qty sizing helpers (identical to the Bybit smoke — shared pattern)
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

@pytest.mark.skipif(not _creds_present(), reason="OKX_DEMO_API_KEY/SECRET/PASSPHRASE not set in .env")
def test_okx_demo_ws_fill_roundtrip(app, monkeypatch) -> None:  # noqa: PLR0915 — intentionally verbose smoke
    """Connect -> open private WS -> marketable BUY -> WS fill -> Account reflects it -> flatten.

    Step 1  resolve_venue_config: proves creds load; asserts demo REST + WS URLs.
    Step 2  instruments-info: parses BTC-USDT filters (tick/step/min_qty/min_notional/base_asset).
    Step 3  connect(): reconcile — seeds LiveOmsHub.account from /api/v5/account/balance.
    Step 4  Start LiveExecutionSession + PrivateUserDataWorker (the WS fill stream).
            Settle ~6s (processEvents + worker.wait) so WS login+subscribe completes before ordering.
            Pong checkpoint: assert NO failed signal fired AND worker.isRunning() — the pong-skip held.
    Step 5  Fetch live ask; compute a tiny BASE qty with notional floor (min_notional=0 workaround).
            Place a MARKET BUY (tgtCcy=base_ccy → qty in BTC, not USDT).
    Step 6  Assert OrderAccepted (REST ACK) with non-empty venue_order_id BEFORE polling.
    Step 7  Poll Qt event loop (processEvents + worker.wait) up to 15 s for the WS fill to land.
    Step 8  Assert: Account BTC position increased AND registry fill qty > 0.
    Step 9  (always in finally) Flatten via opposite MARKET SELL; cancel residual; shutdown.
    """
    pytest.importorskip("PySide6")

    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.coid import CoidMinter
    from vike_trader_app.exec.credentials import Environment
    from vike_trader_app.exec.events import (
        OrderAccepted,
        OrderRejected,
        OrderRequest,
    )
    from vike_trader_app.exec.live_oms import LiveOmsHub
    from vike_trader_app.exec.okx.client import OKXSpotExecutionClient
    from vike_trader_app.exec.okx.instruments import parse_okx_instruments
    from vike_trader_app.exec.okx.transport import okx_public_get, okx_signed_request
    from vike_trader_app.exec.okx.user_data import make_okx_run_core
    from vike_trader_app.exec.order import OrderStatus
    from vike_trader_app.exec.risk import RiskGate, RiskLimits
    from vike_trader_app.exec.venue_config import resolve_venue_config
    from vike_trader_app.ui.private_user_data import LiveExecutionSession, PrivateUserDataWorker

    # --- Step 1: resolve venue config ----------------------------------------------------------
    now_ms = lambda: int(time.time() * 1000)
    cfg = resolve_venue_config("okx", Environment.DEMO, now_ms=now_ms)
    assert cfg is not None, "creds present but venue config did not resolve"
    assert cfg.rest_base_url == "https://www.okx.com", (
        f"unexpected REST: {cfg.rest_base_url}"
    )
    assert cfg.ws_base_url == "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999", (
        f"unexpected WS URL: {cfg.ws_base_url}"
    )

    # --- Step 2: instruments-info (unsigned public) ---------------------------------------------
    # BTC-USDT is the dashed instId form OKX uses everywhere
    info = okx_public_get(
        cfg.rest_base_url,
        "/api/v5/public/instruments",
        {"instType": "SPOT", "instId": "BTC-USDT"},
        simulated=True,
    )
    parsed = parse_okx_instruments(info)
    assert "BTC-USDT" in parsed, f"BTC-USDT not in instruments-info: {list(parsed)[:5]}"
    f = parsed["BTC-USDT"]
    base_asset = f["base_asset"]
    filters = {k: v for k, v in f.items() if k != "base_asset"}

    tick_size = filters.get("tick_size") or 0.01
    step_size = filters.get("step_size") or 0.00001
    min_qty = filters.get("min_qty") or 0.00001
    min_notional = filters.get("min_notional") or 0.0  # will be 0.0 per instruments.py

    # --- Step 3: build bus + client; connect/reconcile -----------------------------------------
    bus = EventBus()
    seen_events: list[object] = []
    bus.subscribe(seen_events.append)

    client = OKXSpotExecutionClient(
        bus,
        signer=cfg.signer,
        rest_base_url=cfg.rest_base_url,
        symbol="BTC-USDT",
        filters=filters,
        base_asset=base_asset,
        transport=functools.partial(okx_signed_request, simulated=True),
        public_transport=functools.partial(okx_public_get, simulated=True),
    )

    snapshot = client.connect()
    assert snapshot.positions, "connect() returned empty positions"

    hub = LiveOmsHub(
        bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
        client=client, venue="okx", symbol="BTC-USDT", now_ms=now_ms,
    )
    hub.apply_snapshot(snapshot)

    # Record starting position size (may already hold BTC from a prior run)
    start_size = (
        hub.account.positions.get(("okx", "BTC-USDT", "BOTH"), {}).get("size", 0.0)
    )

    # --- Fix 1: ensure VIKE_DISABLE_LIVE is unset so add_worker_if_enabled actually starts ------
    # tests/conftest.py sets VIKE_DISABLE_LIVE=1 globally; as a @pytest.mark.network test that
    # genuinely needs the live WS, we must clear it for this test only.
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    # --- Step 4: build the WS worker + session --------------------------------------------------
    # Capture failed signal emissions to assert the pong-skip checkpoint.
    failed_messages: list[str] = []

    session = LiveExecutionSession(hub)
    run_core = make_okx_run_core(
        ws_url=cfg.ws_base_url,
        api_key=cfg.credentials.api_key,
        api_secret=cfg.credentials.api_secret,
        passphrase=cfg.credentials.passphrase or "",
        symbol="BTC-USDT",
        now_ms=now_ms,
    )
    worker = PrivateUserDataWorker(run_core)
    # Wire up failed-signal capture BEFORE add_worker so we don't miss early failures
    worker.failed.connect(failed_messages.append)

    started = session.add_worker_if_enabled("okx", worker)
    assert started, (
        "add_worker_if_enabled returned False — VIKE_DISABLE_LIVE was still set or "
        "worker failed to start; check monkeypatch.delenv above"
    )

    # --- Fix 2: settle for WS subscribe BEFORE placing the order (race guard) ------------------
    # The worker connects, auths, and subscribes ASYNCHRONOUSLY in its QThread.  If the order
    # fills BEFORE the private-channel subscribe completes, OKX will not push the execution
    # frame retroactively (unlike Bybit which has some retro-push).  In production the worker
    # subscribes at session-start long before any order, so this settle is a smoke-only concern.
    # PrivateUserDataWorker exposes no "subscribed" signal, so we pump the event loop for ~6 s
    # — enough for the demo WS to complete auth + subscription handshake.
    settle_deadline = time.monotonic() + 6.0
    while time.monotonic() < settle_deadline:
        app.processEvents()
        worker.wait(200)
        app.processEvents()

    # --- Pong checkpoint (Task-3 Step-0 fix held): assert no crash from OKX 'pong' text frame --
    # If the text 'pong' keepalive triggered a JSON parse error and crashed/reconnected the worker,
    # it would either: (a) emit failed and stop, or (b) still be running but have emitted failed.
    # Both are caught here — NO failed signals AND still running.
    assert not failed_messages, (
        f"worker emitted failed() during settle — pong-handling or auth broke: {failed_messages}"
    )
    assert worker.isRunning(), (
        "PrivateUserDataWorker stopped during 6-s settle — pong or auth crash; "
        "check Task-3 Step-0 skip-non-JSON-keepalive fix in user_data_core.py"
    )

    # ---------------------------------------------------------------------------
    # From here on: try/finally so the demo position is ALWAYS flattened.
    # ---------------------------------------------------------------------------
    coid = CoidMinter().mint()
    flatten_coid: str | None = None
    filled_qty: float = 0.0

    try:
        # --- Step 5: fetch live ask price for qty sizing; place a MARKET BUY -----------------
        ticker_resp = okx_public_get(
            cfg.rest_base_url,
            "/api/v5/market/ticker",
            {"instId": "BTC-USDT"},
            simulated=True,
        )
        ticker_data = ticker_resp.get("data", [{}])
        ask_price = float(
            ticker_data[0].get("askPx") or ticker_data[0].get("last") or 0
        )
        assert ask_price > 0, f"Could not fetch a live ask price from OKX demo: {ticker_resp}"

        # OKX min_notional is 0.0 (hardcoded in instruments.py) — the raw formula
        # max(min_qty, ceil_to_step(0.0 / ask, step)) collapses to min_qty and may be too small.
        # Use an EXPLICIT notional floor of $5 (2x of that = $10 minimum order value) so the
        # demo doesn't reject a sub-dollar order.
        notional_floor = min_notional or 5.0
        raw_qty = max(min_qty, _ceil_to_step((notional_floor * 2) / ask_price, step_size))
        buy_qty = raw_qty  # already step-aligned by _ceil_to_step

        # --- Step 6: submit + assert OrderAccepted BEFORE polling --------------------------
        seen_events.clear()
        request = OrderRequest(
            client_order_id=coid,
            venue="okx",
            symbol="BTC-USDT",
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
                f"ask={ask_price} reason={rejected[0].reason!r}"
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
            pos = hub.account.positions.get(("okx", "BTC-USDT", "BOTH"), {})
            return mo.filled_qty > 0 or pos.get("size", 0.0) > start_size

        while time.monotonic() < deadline and not _fill_landed():
            app.processEvents()
            worker.wait(100)          # yields 100 ms so the QThread can post the queued signal
            app.processEvents()       # pick up any signals posted during the wait

        # --- Step 8: assert fill landed -------------------------------------------------------
        mo = hub.registry.get(coid)
        assert mo is not None, f"coid {coid!r} never reached the registry"

        pos = hub.account.positions.get(("okx", "BTC-USDT", "BOTH"), {})
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
        # flatten_qty = what actually filled (tolerates partial fill or no fill)
        flatten_qty = filled_qty
        if flatten_qty == 0.0:
            # Fallback: check the account position delta directly.
            pos = hub.account.positions.get(("okx", "BTC-USDT", "BOTH"), {})
            flatten_qty = max(0.0, pos.get("size", 0.0) - start_size)

        if flatten_qty > 0.0:
            # Floor to step_size so the SELL qty is venue-valid.
            # On a MARKET SELL, OKX does NOT need tgtCcy — sz is already base units.
            sell_qty = _floor_to_step(flatten_qty, step_size)
            if sell_qty >= min_qty:
                flatten_coid = CoidMinter().mint()
                flatten_req = OrderRequest(
                    client_order_id=flatten_coid,
                    venue="okx",
                    symbol="BTC-USDT",
                    side=-1,
                    qty=sell_qty,
                    order_type="market",
                    price=None,
                )
                try:
                    hub.submit_ticket(flatten_req)
                except Exception:  # noqa: BLE001
                    pass  # best-effort flatten; don't raise and hide the original assertion

        # Cancel any residual resting BUY — OKXSpotExecutionClient.cancel() already swallows
        # the 51400/51401/51402 "not found" family via is_order_not_found().
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
