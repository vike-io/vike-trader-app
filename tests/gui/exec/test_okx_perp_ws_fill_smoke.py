"""LIVE smoke against www.okx.com (x-simulated-trading:1) SWAP + wspap WS — gated by @pytest.mark.network.

Full round-trip (LINEAR PERP): set leverage -> connect/reconcile (signed position) ->
open private WS -> place a SMALL MARKET BUY (SWAP, sz in CONTRACTS via OKXPerpExecutionClient) ->
poll WS execution frames via the Qt event loop until the fill lands in LiveOmsHub.account
-> assert signed LONG position grew AND Account.unrealized_pnl() is computable (mark set
from fill fillMarkPx) -> FLATTEN via sign-aware reduceOnly MARKET order sized from a fresh
reconcile_positions() -> shutdown (worker wait()-joined).

OKX SWAP deltas vs the spot smoke (test_okx_ws_fill_smoke.py):
- Symbol "BTC-USDT-SWAP" EVERYWHERE (dashed SWAP instId form, not compact "BTCUSDT").
- OKXPerpExecutionClient (not OKXSpotExecutionClient); client.set_leverage() called first.
- ctVal: step_size/min_qty from parse_okx_perp_instruments are in CONTRACTS; convert to BASE
  via ct_val: step_in_base = step_size_contracts * ct_val; min_qty_base = min_qty_contracts * ct_val.
- SIZE in BASE (the client converts base->contracts internally via _to_contracts).
- Assert base_qty maps to >= 1 contract: round(base_qty / ct_val) >= 1.
- tdMode=cross; posSide=net (one-way); no tgtCcy; reduceOnly flag.
- Passphrase required (OKX_DEMO_API_PASSPHRASE).
- Pong checkpoint: after WS settle, assert no failed signal AND worker.isRunning().
- make_okx_perp_run_core (not make_okx_run_core) with inst_type="SWAP" + ct_val decoder.
- Cancel swallows codes 51400/51401/51402 (the OKX not-found family, same as spot).
- Flatten = sign-aware: fresh reconcile -> live_size (BASE, signed); if > 0 -> reduceOnly SELL,
  if < 0 -> reduceOnly BUY to cover; floor the ABS(live_size) in BASE via step_in_base.
- Bare RiskGate(RiskLimits()) — NOT min_notional-filtered app limits — so test order is not vetoed.

Run manually:
    PYTHONPATH=src .venv/Scripts/python -m pytest tests/gui/exec/test_okx_perp_ws_fill_smoke.py -m network -v

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
# Qty sizing helpers (byte-identical to the Bybit/OKX spot smokes)
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
def test_okx_demo_perp_ws_fill_roundtrip(app, monkeypatch) -> None:  # noqa: PLR0915 — intentionally verbose smoke
    """set leverage -> connect/reconcile -> open SWAP WS -> MARKET BUY -> WS fill -> flatten.

    Step 1  resolve_venue_config: proves creds load; asserts demo REST + WS URLs.
    Step 2  instruments-info (SWAP): parses BTC-USDT-SWAP filters in CONTRACTS + ct_val;
            convert step_size/min_qty from CONTRACTS to BASE via ct_val; assert >= 1 contract.
    Step 3  OKXPerpExecutionClient.set_leverage(): set to 3x cross (OKX idempotent, swallows '0').
    Step 4  client.connect(): reconcile_positions — seeds LiveOmsHub.account with signed BASE position.
    Step 5  Start LiveExecutionSession + PrivateUserDataWorker with the SWAP run_core (WS fill stream).
    Step 6  Settle ~6 s for WS auth + subscription handshake; pong checkpoint (no failed, still running).
    Step 7  Fetch live ask from /api/v5/market/ticker; size a tiny BASE qty clearing BASE step/min;
            assert round(base_qty / ct_val) >= 1 (maps to at least 1 contract). Place MARKET BUY.
    Step 8  Assert OrderAccepted (REST ACK) with non-empty venue_order_id BEFORE polling.
    Step 9  Poll Qt event loop (processEvents + worker.wait) up to 15 s for WS fill to land.
    Step 10 Assert: Account SWAP position grew (signed BASE) AND unrealized_pnl() is a float.
    Step 11 (always in finally) Sign-aware flatten via fresh reconcile; cancel residual; shutdown.
    """
    pytest.importorskip("PySide6")

    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.coid import CoidMinter
    from vike_trader_app.exec.credentials import Environment
    from vike_trader_app.exec.events import (
        OrderAccepted,
        OrderDenied,
        OrderRejected,
        OrderRequest,
    )
    from vike_trader_app.exec.live_oms import LiveOmsHub
    from vike_trader_app.exec.okx.perp_client import OKXPerpExecutionClient
    from vike_trader_app.exec.okx.perp_instruments import parse_okx_perp_instruments
    from vike_trader_app.exec.okx.perp_user_data import make_okx_perp_run_core
    from vike_trader_app.exec.okx.transport import okx_public_get, okx_signed_request
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

    # --- Step 2: SWAP instruments-info (unsigned public) ----------------------------------------
    # BTC-USDT-SWAP is the dashed SWAP instId form OKX uses everywhere
    info = okx_public_get(
        cfg.rest_base_url,
        "/api/v5/public/instruments",
        {"instType": "SWAP", "instId": "BTC-USDT-SWAP"},
        simulated=True,
    )
    parsed = parse_okx_perp_instruments(info)
    assert "BTC-USDT-SWAP" in parsed, f"BTC-USDT-SWAP not in SWAP instruments-info: {list(parsed)[:5]}"
    f = parsed["BTC-USDT-SWAP"]
    base_asset = f["base_asset"]
    filters = {k: v for k, v in f.items() if k != "base_asset"}

    # CRITICAL: step_size and min_qty from parse_okx_perp_instruments are in CONTRACTS (lotSz/minSz).
    # ct_val converts 1 contract -> BASE units (e.g. 0.01 BTC per contract for BTC-USDT-SWAP).
    # All sizing math for submit/flatten must work in BASE; the client converts base->contracts internally.
    ct_val = filters["ct_val"]
    assert ct_val > 0, f"ct_val must be > 0 for BTC-USDT-SWAP; got {ct_val!r}"
    print(f"\n[smoke] BTC-USDT-SWAP ct_val={ct_val} (expected ~0.01 BTC/contract)")  # noqa: T201 — demo confirmation

    tick_size = filters.get("tick_size") or 0.1
    step_size_contracts = filters.get("step_size") or 1.0    # in CONTRACTS (lotSz)
    min_qty_contracts = filters.get("min_qty") or 1.0        # in CONTRACTS (minSz)

    # Convert CONTRACT steps to BASE steps for all sizing / flatten math.
    step_in_base = step_size_contracts * ct_val   # e.g. 1 contract * 0.01 BTC = 0.01 BTC
    min_qty_base = min_qty_contracts * ct_val      # e.g. 1 contract * 0.01 BTC = 0.01 BTC

    # --- Step 3: build bus + perp client; set leverage before reconcile -----------------------
    bus = EventBus()
    seen_events: list[object] = []
    bus.subscribe(seen_events.append)

    client = OKXPerpExecutionClient(
        bus,
        signer=cfg.signer,
        rest_base_url=cfg.rest_base_url,
        symbol="BTC-USDT-SWAP",
        filters=filters,
        base_asset=base_asset,
        ct_val=ct_val,
        leverage=3.0,
        transport=functools.partial(okx_signed_request, simulated=True),
        public_transport=functools.partial(okx_public_get, simulated=True),
    )

    # set_leverage BEFORE connect() — as required by the perp flow (mirrors app.py).
    # OKX set-leverage is idempotent (returns code '0' on repeat) so no swallowing needed here.
    client.set_leverage()

    # --- Step 4: connect/reconcile — seeds signed BASE position (may be non-zero from prior run) --
    snapshot = client.connect()

    hub = LiveOmsHub(
        bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
        client=client, venue="okx", symbol="BTC-USDT-SWAP", now_ms=now_ms,
    )
    hub.apply_snapshot(snapshot)

    # Record the signed BASE position size BEFORE our order (may be +/0/- from prior run)
    start_size = (
        hub.account.positions.get(("okx", "BTC-USDT-SWAP", "BOTH"), {}).get("size", 0.0)
    )

    # --- Fix 1: ensure VIKE_DISABLE_LIVE is unset so add_worker_if_enabled actually starts ------
    # tests/conftest.py sets VIKE_DISABLE_LIVE=1 globally; as a @pytest.mark.network test that
    # genuinely needs the live WS, we must clear it for this test only.
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    # --- Step 5: build the SWAP WS worker + session --------------------------------------------
    # Capture failed signal emissions to assert the pong-skip checkpoint.
    failed_messages: list[str] = []

    session = LiveExecutionSession(hub)
    run_core = make_okx_perp_run_core(
        ws_url=cfg.ws_base_url,
        api_key=cfg.credentials.api_key,
        api_secret=cfg.credentials.api_secret,
        passphrase=cfg.credentials.passphrase or "",
        symbol="BTC-USDT-SWAP",
        ct_val=ct_val,
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

    # --- Step 6: settle for WS subscribe BEFORE placing the order (race guard) -----------------
    # The worker connects, auths, and subscribes ASYNCHRONOUSLY in its QThread.  If the order
    # fills BEFORE the private-channel subscribe completes, OKX will not push the execution
    # frame retroactively.  In production the worker subscribes at session-start long before any
    # order, so this settle is a smoke-only concern.
    settle_deadline = time.monotonic() + 6.0
    while time.monotonic() < settle_deadline:
        app.processEvents()
        worker.wait(200)
        app.processEvents()

    # --- Pong checkpoint: assert no crash from OKX 'pong' text frame + worker still alive ------
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

    try:
        # --- Step 7: fetch live ask price for qty sizing; place a MARKET BUY ----------------
        ticker_resp = okx_public_get(
            cfg.rest_base_url,
            "/api/v5/market/ticker",
            {"instId": "BTC-USDT-SWAP"},
            simulated=True,
        )
        ticker_data = ticker_resp.get("data", [{}])
        ask_price = float(
            ticker_data[0].get("askPx") or ticker_data[0].get("last") or 0
        )
        assert ask_price > 0, f"Could not fetch a live ask price from OKX demo SWAP: {ticker_resp}"

        # CRITICAL UNIT CONVERSION: size in BASE units (the client converts base->contracts internally).
        # OKX SWAP min_notional is 0.0 so use an explicit $5 notional floor (2x = $10 minimum value).
        # step_in_base and min_qty_base were pre-computed above from CONTRACTS * ct_val.
        notional_floor = 5.0
        raw_qty_base = (notional_floor * 2) / ask_price
        base_qty = max(min_qty_base, _ceil_to_step(raw_qty_base, step_in_base))

        # Non-trivial assertion: base_qty must map to at least 1 contract after the client's
        # _to_contracts() rounding (round(base_qty / ct_val)) — ensures the order is sendable.
        contracts_check = round(base_qty / ct_val)
        assert contracts_check >= 1, (
            f"base_qty={base_qty} maps to {contracts_check} contracts (< 1) — "
            f"increase notional_floor or check ct_val={ct_val}"
        )
        print(  # noqa: T201 — demo confirmation
            f"[smoke] base_qty={base_qty} BTC -> ~{contracts_check} contracts "
            f"(ask={ask_price}, step_in_base={step_in_base}, min_qty_base={min_qty_base})"
        )

        # --- Step 8: submit + assert OrderAccepted BEFORE polling --------------------------
        seen_events.clear()
        request = OrderRequest(
            client_order_id=coid,
            venue="okx",
            symbol="BTC-USDT-SWAP",
            side=+1,
            qty=base_qty,
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
                f"coid={coid!r} qty={base_qty} reason={denied[0].reason!r}"
            )

        rejected = [e for e in seen_events if isinstance(e, OrderRejected)]
        if rejected:
            pytest.skip(
                f"demo rejected the test order: coid={coid!r} qty={base_qty} "
                f"ask={ask_price} reason={rejected[0].reason!r}"
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
            pos = hub.account.positions.get(("okx", "BTC-USDT-SWAP", "BOTH"), {})
            # filled_qty > 0 (BASE) OR account position grew vs start
            return mo.filled_qty > 0 or pos.get("size", 0.0) > start_size

        while time.monotonic() < deadline and not _fill_landed():
            app.processEvents()
            worker.wait(100)          # yields 100 ms so the QThread can post the queued signal
            app.processEvents()       # pick up any signals posted during the wait

        # --- Step 10: assert fill landed + signed LONG position + mark recorded -------------
        mo = hub.registry.get(coid)
        assert mo is not None, f"coid {coid!r} never reached the registry"

        pos = hub.account.positions.get(("okx", "BTC-USDT-SWAP", "BOTH"), {})
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

        # Signed LONG assertion: a SWAP BUY must open/grow a positive BASE position.
        assert actual_size > start_size, (
            f"Account position did not grow after SWAP BUY fill: "
            f"start_size={start_size} actual_size={actual_size}"
        )

        # Mark price was set from the fill's fillMarkPx field (perp_mapper enrichment).
        # unrealized_pnl() returns a float if and only if mark was recorded — proves the
        # map_okx_perp rescale + mark carry landed correctly in the Account.
        upnl = hub.account.unrealized_pnl("okx", "BTC-USDT-SWAP")
        assert isinstance(upnl, float), (
            f"unrealized_pnl() returned {upnl!r} — mark not recorded from fill fillMarkPx"
        )
        # upnl may be 0.0 exactly (mark == avg_px) but isinstance(0.0, float) is True.

    finally:
        # --- Step 11: SIGN-AWARE FLATTEN + cancel residual + shut down --------------------
        # Re-reconcile to read the TRUE live signed BASE size (handles partial fill + prior runs).
        # A residual SHORT (live_size < 0) from an earlier aborted long also gets flattened.
        try:
            live_snapshot = client.connect()
            live_size = dict(live_snapshot.positions).get("BTC-USDT-SWAP", 0.0)
        except Exception:  # noqa: BLE001
            # If reconcile itself fails, fall back to the account's position delta
            pos = hub.account.positions.get(("okx", "BTC-USDT-SWAP", "BOTH"), {})
            live_size = pos.get("size", 0.0)

        if live_size != 0.0:
            # SIGN-AWARE: SELL a long; BUY to cover a short.
            flatten_side = -1 if live_size > 0 else +1
            # Floor ABS(live_size) in BASE via step_in_base (NOT step_size_contracts — 100x off!).
            flatten_qty = _floor_to_step(abs(live_size), step_in_base)
            if flatten_qty >= min_qty_base:
                flatten_coid = CoidMinter().mint()
                flatten_req = OrderRequest(
                    client_order_id=flatten_coid,
                    venue="okx",
                    symbol="BTC-USDT-SWAP",
                    side=flatten_side,
                    qty=flatten_qty,
                    order_type="market",
                    price=None,
                    reduce_only=True,
                )
                try:
                    hub.submit_ticket(flatten_req)
                except Exception:  # noqa: BLE001
                    pass  # best-effort flatten; don't raise and hide the original assertion

        # Cancel any residual resting order — OKXPerpExecutionClient inherits OKXSpotExecutionClient
        # which already swallows the 51400/51401/51402 "not found" family via is_order_not_found().
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
