"""LIVE smoke against api-demo.bybit.com + stream-demo.bybit.com — gated by @pytest.mark.network.

Full round-trip: connect/reconcile -> open private WS -> place a SMALL MARKET BUY
-> poll WS execution frames via the Qt event loop until the fill lands in
LiveOmsHub.account -> assert position moved -> FLATTEN and clean up.

A LIMIT buy at ask*1.001 rests on Bybit spot (never fills), so the smoke uses a MARKET BUY
(category=spot, marketUnit=baseCoin) which fills instantly and streams the execution frame.

This test GENUINELY FILLS a demo order and ALWAYS cleans up via try/finally.

Run manually:
    PYTHONPATH=src .venv/Scripts/python -m pytest tests/gui/exec/test_bybit_ws_fill_smoke.py -m network -v

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
# Qty sizing helpers
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
def test_bybit_demo_ws_fill_roundtrip(app) -> None:  # noqa: PLR0915 — intentionally verbose smoke
    """Connect -> open private WS -> marketable BUY -> WS fill -> Account reflects it -> flatten.

    Step 1  resolve_venue_config: proves creds load; asserts demo WS URL.
    Step 2  instruments-info: parses BTCUSDT filters (tick/step/min_notional/min_qty).
    Step 3  connect(): reconcile — seeds LiveOmsHub.account from walletBalance.
    Step 4  Start LiveExecutionSession + PrivateUserDataWorker (the WS fill stream).
    Step 5  Compute a tiny base qty; place a MARKET BUY (fills instantly on Bybit spot).
    Step 6  Assert OrderAccepted (REST ACK) before polling — a rejected order never fills.
    Step 7  Poll Qt event loop (processEvents + worker.wait) up to 15 s for the WS fill.
    Step 8  Assert: Account BTC position increased AND registry fill qty > 0.
    Step 9  (always in finally) Flatten via opposite MARKET SELL; cancel residual; shutdown.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from decimal import Decimal, ROUND_DOWN

    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.binance.transport import get_public_json
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.bybit.client import BybitSpotExecutionClient
    from vike_trader_app.exec.bybit.instruments import parse_bybit_instruments_info
    from vike_trader_app.exec.bybit.user_data import make_bybit_run_core
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
    cfg = resolve_venue_config("bybit", Environment.DEMO, now_ms=now_ms)
    assert cfg is not None, "creds present but venue config did not resolve"
    assert cfg.rest_base_url == "https://api-demo.bybit.com", (
        f"unexpected REST: {cfg.rest_base_url}"
    )
    assert cfg.ws_base_url == "wss://stream-demo.bybit.com/v5/private", (
        f"unexpected WS URL: {cfg.ws_base_url}"
    )

    # --- Step 2: instruments-info (unsigned) ----------------------------------------------------
    info = get_public_json(cfg.rest_base_url, "/v5/market/instruments-info",
                           {"category": "spot", "symbol": "BTCUSDT"})
    parsed = parse_bybit_instruments_info(info)
    assert "BTCUSDT" in parsed, f"BTCUSDT not in instruments-info: {list(parsed)[:5]}"
    f = parsed["BTCUSDT"]
    base_asset = f["base_asset"]
    filters = {k: v for k, v in f.items() if k != "base_asset"}

    tick_size = filters.get("tick_size") or 0.01
    step_size = filters.get("step_size") or 0.000001
    min_qty = filters.get("min_qty") or 0.0001
    min_notional = filters.get("min_notional") or 5.0

    # --- Step 3: build bus + client; connect/reconcile -----------------------------------------
    bus = EventBus()
    seen_events: list[object] = []
    bus.subscribe(seen_events.append)

    client = BybitSpotExecutionClient(
        bus, signer=cfg.signer, rest_base_url=cfg.rest_base_url,
        symbol="BTCUSDT", filters=filters, base_asset=base_asset,
    )

    snapshot = client.connect()
    assert snapshot.positions, "connect() returned empty positions"

    hub = LiveOmsHub(
        bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
        client=client, venue="bybit", symbol="BTCUSDT", now_ms=now_ms,
    )
    hub.apply_snapshot(snapshot)

    # Record starting position size (may already hold BTC from a prior run)
    start_size = (
        hub.account.positions.get(("bybit", "BTCUSDT", "BOTH"), {}).get("size", 0.0)
    )

    # --- Step 4: build the WS worker + session --------------------------------------------------
    session = LiveExecutionSession(hub)
    run_core = make_bybit_run_core(
        ws_url=cfg.ws_base_url,
        api_key=cfg.credentials.api_key,
        api_secret=cfg.credentials.api_secret,
        symbol="BTCUSDT",
        now_ms=now_ms,
    )
    worker = PrivateUserDataWorker(run_core)
    session.add_worker_if_enabled("bybit", worker)

    # ---------------------------------------------------------------------------
    # From here on: try/finally so the demo position is ALWAYS flattened.
    # ---------------------------------------------------------------------------
    coid = CoidMinter().mint()
    flatten_coid: str | None = None
    filled_qty: float = 0.0

    try:
        # --- Step 5: fetch live ask price for qty sizing; place a MARKET BUY -----------------
        ticker = get_public_json(cfg.rest_base_url, "/v5/market/tickers",
                                 {"category": "spot", "symbol": "BTCUSDT"})
        ask_price = float(ticker["result"]["list"][0].get("ask1Price") or
                          ticker["result"]["list"][0]["lastPrice"])

        # qty = max(min_qty, ceil_to_step(min_notional * 2 / price, step_size))
        # This satisfies BOTH minOrderQty AND minOrderAmt.
        # For a MARKET BUY with marketUnit=baseCoin, qty is in base asset — no price needed.
        raw_qty = max(min_qty, _ceil_to_step((min_notional * 2) / ask_price, step_size))
        buy_qty = raw_qty  # already step-aligned by _ceil_to_step

        # --- Step 6: submit + assert OrderAccepted BEFORE polling --------------------------
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
        rejected = [e for e in seen_events if isinstance(e, OrderRejected)]
        if rejected:
            pytest.skip(
                f"demo rejected the test order: coid={coid!r} qty={buy_qty} "
                f"price={buy_price} reason={rejected[0].reason!r}"
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
            pos = hub.account.positions.get(("bybit", "BTCUSDT", "BOTH"), {})
            return mo.filled_qty > 0 or pos.get("size", 0.0) > start_size

        while time.monotonic() < deadline and not _fill_landed():
            app.processEvents()
            worker.wait(100)          # yields 100 ms so the QThread can post the queued signal
            app.processEvents()       # pick up any signals posted during the wait

        # --- Step 8: assert fill landed -------------------------------------------------------
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

        filled_qty = mo.filled_qty

    finally:
        # --- Step 9: FLATTEN + cancel residual + shut down --------------------------------
        # flatten_qty = what actually filled (tolerates partial fill or no fill)
        flatten_qty = filled_qty
        if flatten_qty == 0.0:
            # Fallback: check the account position delta directly.
            pos = hub.account.positions.get(("bybit", "BTCUSDT", "BOTH"), {})
            flatten_qty = max(0.0, pos.get("size", 0.0) - start_size)

        if flatten_qty > 0.0:
            # Floor to step_size so the SELL qty is venue-valid.
            sell_qty = _floor_to_step(flatten_qty, step_size)
            if sell_qty >= min_qty:
                flatten_coid = CoidMinter().mint()
                flatten_req = OrderRequest(
                    client_order_id=flatten_coid,
                    venue="bybit",
                    symbol="BTCUSDT",
                    side=-1,
                    qty=sell_qty,
                    order_type="market",
                    price=None,
                )
                try:
                    hub.submit_ticket(flatten_req)
                except Exception:  # noqa: BLE001
                    pass  # best-effort flatten; don't raise and hide the original assertion

        # Cancel any residual resting BUY (swallows 110001/170213 "not found")
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
