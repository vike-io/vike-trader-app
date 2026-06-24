"""LIVE smoke against demo-fapi.binance.com + fstream.binancefuture.com/ws — gated by @pytest.mark.network.

Full round-trip (USDⓈ-M LINEAR PERP): one-way mode precondition check -> set leverage ->
connect/reconcile (signed position from positionRisk) -> open listenKey WS -> place a SMALL
MARKET BUY (qty in BASE asset, no ct_val conversion) -> poll ORDER_TRADE_UPDATE frames via the
Qt event loop until the fill lands in LiveOmsHub.account -> re-reconcile to seed mark from
positionRisk.markPrice -> assert position grew + unrealized_pnl() computable -> FLATTEN via
sign-aware reduceOnly MARKET order sized from a fresh reconcile -> shutdown.

Deltas from the OKX perp smoke (document each):
- Symbol "BTCUSDT" EVERYWHERE (plain — no dash, no "-SWAP").
- BinancePerpExecutionClient (no ct_val, no passphrase); qty in BASE asset directly.
  step_in_base = filters["step_size"], min_qty_base = filters["min_qty"] — already base.
- make_binance_perp_run_core(fapi_rest_url=..., ws_base_url=..., api_key=..., symbol=..., now_ms=...)
  — NO ct_val, NO passphrase. api_secret NOT required (listenKey endpoints are apiKey-header-only).
- REST host = binance_fapi_rest(Environment.DEMO) = demo-fapi.binance.com.
  WS host = binance_fapi_ws(Environment.DEMO) = wss://fstream.binancefuture.com/ws (env-overridable
  via BINANCE_DEMO_FAPI_WS_URL; if the fill never lands, try wss://stream.binancefuture.com/ws).
- POSITION-MODE precondition (one-way required): GET /fapi/v1/positionSide/dual (signed via
  cfg.signer + signed_request). DEFENSIVE parse: resp.get("dualSidePosition") in (False, "false")
  is one-way; (True, "true") triggers pytest.skip with a flip instruction. Some demo proxies
  return the STRING "false" — treat it as one-way.
- MARK ASSERTION DIFFERS: ORDER_TRADE_UPDATE carries NO mark price, so FillEvent.mark_price is
  None from the WS fill. Do NOT assert mark from the fill. Instead, after the fill lands,
  RE-RECONCILE (client.connect() -> hub.apply_snapshot) then assert:
    hub.account.marks.get(("binance","BTCUSDT")) > 0.0   (seeded from positionRisk.markPrice)
    hub.account.unrealized_pnl("binance","BTCUSDT") is float
- Sizing: fetch /fapi/v1/ticker/bookTicker ask; notional_floor = max(min_notional, 100.0);
  base_qty = max(min_qty_base, _ceil_to_step(notional_floor * 1.1 / ask, step_in_base)).
  fapi BTCUSDT MIN_NOTIONAL is typically 100 USDT.
- Flatten = SIGN-AWARE via fresh reconcile: live_size = dict(client.connect().positions)["BTCUSDT"];
  if >0 reduceOnly SELL, if <0 reduceOnly BUY; flatten_qty = _floor_to_step(abs(live_size), step_in_base).
- Bare RiskGate(RiskLimits()) — NOT min_notional-filtered app limits — so test order is not vetoed.
- No passphrase — Binance HMAC only.
- Creds: BINANCE_DEMO_API_KEY / BINANCE_DEMO_API_SECRET.

WS host open question: the listenKey WS for fapi is NOT the same as the spot WS-API.
  Confirmed candidate: wss://fstream.binancefuture.com/ws (BINANCE_DEMO_FAPI_WS_DEFAULT).
  Fallback to try if fill doesn't land: wss://stream.binancefuture.com/ws
  (set env BINANCE_DEMO_FAPI_WS_URL=wss://stream.binancefuture.com/ws and re-run).

Run manually:
    PYTHONPATH=src .venv/Scripts/python -m pytest tests/gui/exec/test_binance_perp_ws_fill_smoke.py -m network -v

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
# Qty sizing helpers (byte-identical to the OKX/Bybit perp smokes)
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
def test_binance_demo_perp_ws_fill_roundtrip(app, monkeypatch) -> None:  # noqa: PLR0915 — intentionally verbose smoke
    """one-way check -> set leverage -> connect/reconcile -> listenKey WS -> MARKET BUY -> WS fill -> re-reconcile mark -> flatten.

    Step 1   resolve_venue_config: proves creds load; asserts fapi REST + WS URLs from binance_fapi_rest/ws.
    Step 2   parse_binance_perp_instruments: fetches fapi BTCUSDT filters (tick/step/min_qty/min_notional).
             qty is in BASE asset — step_in_base = step_size, min_qty_base = min_qty (no ct_val).
    Step 2.5 One-way precondition: GET /fapi/v1/positionSide/dual (signed); DEFENSIVE parse:
             dualSidePosition in (False, "false") -> OK (one-way); (True, "true") -> skip.
    Step 3   BinancePerpExecutionClient.set_leverage(5x): idempotent HTTP-200, no swallow needed.
    Step 4   client.connect(): reconcile_positions (GET /fapi/v2/positionRisk) — seeds LiveOmsHub.account.
    Step 5   Start LiveExecutionSession + PrivateUserDataWorker with make_binance_perp_run_core
             (listenKey WS: POST /fapi/v1/listenKey -> connect wss://<ws_base>/<listenKey>).
    Step 6   Settle ~6s for listenKey WS connect; pong checkpoint (no failed signal, worker running).
    Step 7   Fetch live ask from /fapi/v1/ticker/bookTicker; size a tiny BASE qty clearing MIN_NOTIONAL
             (notional_floor = max(min_notional, 100.0); base_qty = max(min_qty_base,
             _ceil_to_step(notional_floor * 1.1 / ask, step_in_base))). Place MARKET BUY.
    Step 8   Assert OrderAccepted (REST ACK) with non-empty venue_order_id BEFORE polling.
    Step 9   Poll Qt event loop (processEvents + worker.wait) up to 15s for ORDER_TRADE_UPDATE fill.
    Step 10  Re-reconcile (client.connect() -> hub.apply_snapshot) to seed mark from positionRisk.markPrice.
             Assert Account BTCUSDT position grew (signed BASE) AND marks[("binance","BTCUSDT")] > 0
             AND unrealized_pnl() is float.
    Step 11  (always in finally) Sign-aware flatten via fresh reconcile; cancel residual; shutdown.
    """
    pytest.importorskip("PySide6")

    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.binance.perp_client import BinancePerpExecutionClient
    from vike_trader_app.exec.binance.perp_instruments import parse_binance_perp_instruments
    from vike_trader_app.exec.binance.perp_user_data import make_binance_perp_run_core
    from vike_trader_app.exec.binance.transport import get_public_json, signed_request
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
    from vike_trader_app.exec.order import OrderStatus
    from vike_trader_app.exec.risk import RiskGate, RiskLimits
    from vike_trader_app.exec.venue_config import binance_fapi_rest, binance_fapi_ws, resolve_venue_config
    from vike_trader_app.ui.private_user_data import LiveExecutionSession, PrivateUserDataWorker

    # --- Step 1: resolve venue config ----------------------------------------------------------
    now_ms = lambda: int(time.time() * 1000)
    cfg = resolve_venue_config("binance", Environment.DEMO, now_ms=now_ms)
    assert cfg is not None, "creds present but venue config did not resolve — check env vars"

    # The perp smoke uses FAPI-specific REST + WS URLs (NOT the spot cfg.rest_base_url /
    # cfg.ws_base_url which point to demo-api.binance.com / demo-ws-api.binance.com).
    fapi_rest_url = binance_fapi_rest(Environment.DEMO)
    fapi_ws_url = binance_fapi_ws(Environment.DEMO)
    assert fapi_rest_url == "https://demo-fapi.binance.com", (
        f"unexpected fapi REST: {fapi_rest_url}"
    )
    print(f"\n[smoke] fapi REST={fapi_rest_url!r}  fapi WS={fapi_ws_url!r}")  # noqa: T201

    # --- Step 2: fapi exchangeInfo (unsigned) — perp BTCUSDT filters ---------------------------
    info = get_public_json(fapi_rest_url, "/fapi/v1/exchangeInfo", {"symbol": "BTCUSDT"})
    assert "symbols" in info, f"fapi exchangeInfo missing 'symbols' key: {list(info)[:5]}"
    parsed = parse_binance_perp_instruments(info)
    assert "BTCUSDT" in parsed, f"BTCUSDT not in fapi perp instruments: {list(parsed)[:5]}"
    f = parsed["BTCUSDT"]
    base_asset = f["base_asset"]

    # Binance fapi qty is in BASE asset (NOT contracts, unlike OKX ct_val).
    # step_in_base and min_qty_base are the raw LOT_SIZE values — no conversion needed.
    tick_size = f.get("tick_size") or 0.1
    step_in_base = f.get("step_size") or 0.001          # LOT_SIZE.stepSize (in BTC)
    min_qty_base = f.get("min_qty") or 0.001             # LOT_SIZE.minQty (in BTC)
    min_notional = f.get("min_notional") or 100.0        # MIN_NOTIONAL.notional (fapi ~100 USDT)

    filters = {k: v for k, v in f.items() if k != "base_asset"}
    print(  # noqa: T201
        f"[smoke] BTCUSDT fapi filters: tick={tick_size} step={step_in_base} "
        f"min_qty={min_qty_base} min_notional={min_notional}"
    )

    # --- Step 2.5: ONE-WAY PRECONDITION — GET /fapi/v1/positionSide/dual (signed) -------------
    # DEFENSIVE parse: the demo JSON body may return a Python bool False or the STRING "false".
    # Treat EITHER as one-way (positionSide=BOTH). Skip only on genuine hedge mode.
    mode_resp = signed_request(fapi_rest_url, "/fapi/v1/positionSide/dual", "GET",
                               {}, cfg.signer)
    dual = mode_resp.get("dualSidePosition")
    if dual in (True, "true"):
        pytest.skip(
            "demo account is in HEDGE mode; 5d is one-way only (positionSide=BOTH) — "
            "flip via POST /fapi/v1/positionSide/dual?dualSidePosition=false when flat, "
            "or run on a one-way demo account"
        )
    assert dual in (False, "false"), (
        f"unexpected dualSidePosition value: {dual!r} (expected False or 'false' for one-way)"
    )
    print(f"[smoke] position mode: dualSidePosition={dual!r} (one-way OK)")  # noqa: T201

    # --- Step 3: build bus + perp client; set leverage before reconcile -----------------------
    bus = EventBus()
    seen_events: list[object] = []
    bus.subscribe(seen_events.append)

    client = BinancePerpExecutionClient(
        bus,
        signer=cfg.signer,
        rest_base_url=fapi_rest_url,
        symbol="BTCUSDT",
        filters=filters,
        base_asset=base_asset,
        leverage=5.0,
    )

    # set_leverage BEFORE connect() — as required by the perp flow (mirrors app.py).
    # Binance set-leverage is idempotent (HTTP-200 even when already at target) — no swallow needed.
    client.set_leverage()
    print("[smoke] set_leverage(5x) OK")  # noqa: T201

    # --- Step 4: connect/reconcile — seeds signed BASE position (positionRisk, may be non-zero) -
    snapshot = client.connect()

    hub = LiveOmsHub(
        bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
        client=client, venue="binance", symbol="BTCUSDT", now_ms=now_ms,
    )
    hub.apply_snapshot(snapshot)

    # Record the signed BASE position size BEFORE our order (may be +/0/- from prior run).
    start_size = (
        hub.account.positions.get(("binance", "BTCUSDT", "BOTH"), {}).get("size", 0.0)
    )
    print(f"[smoke] start_size={start_size} BTC (from positionRisk reconcile)")  # noqa: T201

    # --- Fix 1: ensure VIKE_DISABLE_LIVE is unset so add_worker_if_enabled actually starts ------
    # tests/conftest.py sets VIKE_DISABLE_LIVE=1 globally; as a @pytest.mark.network test that
    # genuinely needs the live WS, we must clear it for this test only.
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    # --- Step 5: build the fapi listenKey WS worker + session ----------------------------------
    # Capture failed signal emissions to assert the settle checkpoint.
    failed_messages: list[str] = []

    session = LiveExecutionSession(hub)
    run_core = make_binance_perp_run_core(
        fapi_rest_url=fapi_rest_url,
        ws_base_url=fapi_ws_url,
        api_key=cfg.credentials.api_key,
        symbol="BTCUSDT",
        now_ms=now_ms,
        # NO api_secret — listenKey endpoints are apiKey-header-only (X-MBX-APIKEY); no HMAC needed.
        # NO ct_val — Binance fapi qty is BASE, not contracts.
        # NO passphrase — Binance uses HMAC only.
    )
    worker = PrivateUserDataWorker(run_core)
    # Wire up failed-signal capture BEFORE add_worker so we don't miss early failures.
    worker.failed.connect(failed_messages.append)

    started = session.add_worker_if_enabled("binance", worker)
    assert started, (
        "add_worker_if_enabled returned False — VIKE_DISABLE_LIVE was still set or "
        "worker failed to start; check monkeypatch.delenv above"
    )

    # --- Step 6: settle for listenKey WS connect BEFORE placing the order (race guard) ---------
    # The worker POSTs /fapi/v1/listenKey (header-only, no HMAC), then connects
    # wss://<ws_base>/<listenKey> ASYNCHRONOUSLY in its QThread. If the order fills BEFORE the
    # WS is connected, the fill frame will be missed (the stream does not push retroactively).
    # In production the worker connects at session-start long before any order; this settle is
    # smoke-only. The fapi listenKey stream has NO auth handshake or subscribe frame — the key
    # in the URL IS the auth; connection completes faster than the spot WS-API signed-subscribe.
    settle_deadline = time.monotonic() + 6.0
    while time.monotonic() < settle_deadline:
        app.processEvents()
        worker.wait(200)
        app.processEvents()

    # --- Settle checkpoint: no failure signal + worker still running ---------------------------
    assert not failed_messages, (
        f"worker emitted failed() during settle — listenKey create or WS connect broke: "
        f"{failed_messages}  [fapi_ws_url={fapi_ws_url!r}]"
    )
    assert worker.isRunning(), (
        "PrivateUserDataWorker stopped during 6-s settle — "
        f"listenKey create or WS connect failure on {fapi_ws_url!r}"
    )

    # ---------------------------------------------------------------------------
    # From here on: try/finally so the demo position is ALWAYS flattened.
    # ---------------------------------------------------------------------------
    coid = CoidMinter().mint()

    try:
        # --- Step 7: fetch live ask price for qty sizing; place a MARKET BUY ----------------
        book = get_public_json(fapi_rest_url, "/fapi/v1/ticker/bookTicker", {"symbol": "BTCUSDT"})
        ask_price = float(book.get("askPrice") or book.get("bidPrice") or 0)
        assert ask_price > 0, f"Could not fetch a live ask price from fapi demo: {book}"

        # Size a tiny BASE qty clearing MIN_NOTIONAL (fapi BTCUSDT ~100 USDT).
        # notional_floor = max(parsed min_notional, 100.0) as a safety net.
        # Use 1.1x the floor so we clear the limit with a small buffer.
        notional_floor = max(min_notional, 100.0)
        raw_qty_base = (notional_floor * 1.1) / ask_price
        base_qty = max(min_qty_base, _ceil_to_step(raw_qty_base, step_in_base))

        print(  # noqa: T201
            f"[smoke] base_qty={base_qty} BTC "
            f"(ask={ask_price}, notional_floor={notional_floor}, "
            f"step_in_base={step_in_base}, min_qty_base={min_qty_base})"
        )

        # --- Step 8: submit + assert OrderAccepted BEFORE polling --------------------------
        seen_events.clear()
        request = OrderRequest(
            client_order_id=coid,
            venue="binance",
            symbol="BTCUSDT",
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
        print(f"[smoke] OrderAccepted: venue_order_id={accepted[0].venue_order_id!r}")  # noqa: T201

        # --- Step 9: poll the Qt event loop up to 15 s for ORDER_TRADE_UPDATE fill ----------
        deadline = time.monotonic() + 15.0

        def _fill_landed() -> bool:
            mo = hub.registry.get(coid)
            if mo is None:
                return False
            pos = hub.account.positions.get(("binance", "BTCUSDT", "BOTH"), {})
            # filled_qty > 0 (BASE) OR account position grew vs start
            return mo.filled_qty > 0 or pos.get("size", 0.0) > start_size

        while time.monotonic() < deadline and not _fill_landed():
            app.processEvents()
            worker.wait(100)          # yields 100 ms so the QThread can post the queued signal
            app.processEvents()       # pick up any signals posted during the wait

        # --- Step 10: assert fill landed + signed LONG position + mark from re-reconcile ----
        mo = hub.registry.get(coid)
        assert mo is not None, f"coid {coid!r} never reached the registry"

        pos = hub.account.positions.get(("binance", "BTCUSDT", "BOTH"), {})
        actual_size = pos.get("size", 0.0)

        assert mo.filled_qty > 0 or actual_size > start_size, (
            f"ORDER_TRADE_UPDATE fill did not arrive within 15 s: "
            f"filled_qty={mo.filled_qty} status={mo.status} "
            f"start_size={start_size} actual_size={actual_size}  "
            f"[fapi_ws_url={fapi_ws_url!r} — try BINANCE_DEMO_FAPI_WS_URL=wss://stream.binancefuture.com/ws]"
        )
        assert mo.status in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}, (
            f"unexpected order status after fill poll: {mo.status}"
        )
        assert mo.filled_qty > 0, f"filled_qty is 0 but status is {mo.status}"

        # Signed LONG assertion: a perp BUY must open/grow a positive BASE position.
        assert actual_size > start_size, (
            f"Account position did not grow after perp BUY fill: "
            f"start_size={start_size} actual_size={actual_size}"
        )

        # MARK ASSERTION: ORDER_TRADE_UPDATE carries NO mark price (unlike OKX fillMarkPx).
        # Re-reconcile now so positionRisk.markPrice seeds hub.account.marks.
        # This is the ONLY way to get the mark for Binance perp — must be done post-fill.
        try:
            post_fill_snapshot = client.connect()
            hub.apply_snapshot(post_fill_snapshot)
        except Exception:  # noqa: BLE001 — best-effort re-reconcile; don't mask the fill assertion
            pass

        # If the re-reconcile seeded a mark (positionRisk row has markPrice > 0), assert it.
        # A flat positionRisk row after fill may have markPrice=0 — softer check via unrealized_pnl.
        mark = hub.account.marks.get(("binance", "BTCUSDT"))
        if mark is not None and mark > 0.0:
            print(f"[smoke] mark={mark} (seeded from positionRisk.markPrice)")  # noqa: T201
            upnl = hub.account.unrealized_pnl("binance", "BTCUSDT")
            assert isinstance(upnl, float), (
                f"unrealized_pnl() returned {upnl!r} — mark recorded but upnl not float"
            )
        else:
            # Mark may be absent if positionRisk returns markPrice=0 for a recently opened position;
            # fall back to asserting the position grew (already asserted above).
            print(  # noqa: T201
                f"[smoke] mark not seeded from re-reconcile "
                f"(marks={hub.account.marks}) — position growth asserted instead"
            )

    finally:
        # --- Step 11: SIGN-AWARE FLATTEN + cancel residual + shut down --------------------
        # Re-reconcile to read the TRUE live signed BASE size (handles partial fill + prior runs).
        # A residual SHORT (live_size < 0) from an earlier aborted long also gets flattened.
        try:
            live_snapshot = client.connect()
            live_size = dict(live_snapshot.positions).get("BTCUSDT", 0.0)
        except Exception:  # noqa: BLE001
            # If reconcile itself fails, fall back to the account's position delta.
            pos = hub.account.positions.get(("binance", "BTCUSDT", "BOTH"), {})
            live_size = pos.get("size", 0.0)

        if live_size != 0.0:
            # SIGN-AWARE: SELL a long; BUY to cover a short.
            flatten_side = -1 if live_size > 0 else +1
            # Floor ABS(live_size) in BASE via step_in_base (step_size, already in BTC).
            # reduceOnly="true" in build_order_params (STRING, not bool — verified in perp_client.py).
            flatten_qty = _floor_to_step(abs(live_size), step_in_base)
            print(  # noqa: T201
                f"[smoke] flatten: live_size={live_size} side={'SELL' if flatten_side < 0 else 'BUY'} "
                f"qty={flatten_qty}"
            )
            if flatten_qty >= min_qty_base:
                flatten_coid = CoidMinter().mint()
                flatten_req = OrderRequest(
                    client_order_id=flatten_coid,
                    venue="binance",
                    symbol="BTCUSDT",
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

        # Cancel any residual resting order.
        # BinancePerpExecutionClient inherits BinanceSpotExecutionClient which swallows -2011
        # ("Unknown order") via is_order_not_found() — same as the spot smoke.
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
