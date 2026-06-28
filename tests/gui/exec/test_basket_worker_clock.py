"""Basket worker now_ms clock test — verifies wall-clock (not epoch-0) in _register_basket_worker.

CRITICAL fix for: `now_ms=lambda: 0` in every branch of `_register_basket_worker` was replaced
with `now_ms=lambda: int(time.time()*1000)`.  This test asserts that the now_ms passed to the
per-venue run_core factory is wall-clock accurate (within a few seconds), NOT 0.

Offline — the run_core factory is intercepted before building the real coroutine; we only
check the now_ms callable that would have been handed to the WS connection.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _setup_bybit_spot_mocks(monkeypatch, ws_base_url="wss://stream.bybit.com"):
    """Patch resolve_venue_config + instruments GET + client.connect() for bybit spot."""
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import BybitV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig

    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    creds = Credentials(api_key="K", api_secret="S")
    cfg = VenueConfig(
        venue="bybit", environment=Environment.DEMO,
        rest_base_url="https://api-demo.bybit.com",
        ws_base_url=ws_base_url,
        credentials=creds,
        signer=BybitV5Signer(creds, now_ms=lambda: 0),
    )

    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    from vike_trader_app.exec.bybit import client as bybit_client_mod
    monkeypatch.setattr(
        bybit_client_mod.BybitSpotExecutionClient, "connect",
        lambda self: __import__(
            "vike_trader_app.exec.crypto_client", fromlist=["ReconcileSnapshot"]
        ).ReconcileSnapshot(
            positions=((self._symbol, 0.0),),
            position_avg_px=((self._symbol, 0.0),),
        ),
    )

    import vike_trader_app.exec.binance.transport as btransport

    def _fake_public(base_url, path, params=None):
        sym = (params or {}).get("symbol", "BTCUSDT")
        return {
            "retCode": 0,
            "result": {"list": [
                {
                    "symbol": sym, "baseCoin": sym[:3],
                    "priceFilter": {"tickSize": "0.01"},
                    "lotSizeFilter": {
                        "basePrecision": "0.000001", "minOrderQty": "0.0001",
                        "maxOrderQty": "100", "minOrderAmt": "1",
                    },
                }
            ]},
        }

    monkeypatch.setattr(btransport, "get_public_json", _fake_public)
    return cfg


def test_basket_worker_now_ms_is_wall_clock_bybit_spot(app, monkeypatch):
    """The now_ms callable passed to the bybit run_core builder in _register_basket_worker
    must return a wall-clock timestamp (within ±5 seconds of time.time()*1000), NOT 0.

    This is the CRITICAL fix: `now_ms=lambda: 0` → `now_ms=lambda: int(time.time()*1000)`.
    A zero now_ms produces an epoch-1970 auth frame that exchanges reject immediately.

    Strategy: intercept make_bybit_run_core and add_worker_if_enabled so we capture the
    now_ms callable WITHOUT needing to construct a real PrivateUserDataWorker (which requires
    Qt signals).  The run_core factory spy records the kwargs; add_worker_if_enabled is a no-op.
    """
    _setup_bybit_spot_mocks(monkeypatch)

    # Capture the now_ms passed to make_bybit_run_core calls (one per basket symbol).
    captured_now_ms: list = []

    from vike_trader_app.exec.bybit import user_data as bybit_ud

    def _spy_make(*args, **kwargs):
        nm = kwargs.get("now_ms")
        if nm is not None:
            captured_now_ms.append(nm)
        # Return a sentinel coroutine-function (run_core is just a callable; PrivateUserDataWorker
        # wraps it but we intercept add_worker_if_enabled below before the worker is started).
        async def _noop_core(*a, **k):
            pass
        return _noop_core

    monkeypatch.setattr(bybit_ud, "make_bybit_run_core", _spy_make)

    # Suppress add_worker_if_enabled so the fake run_core is never handed to a real QThread.
    from vike_trader_app.ui import private_user_data as pud_mod
    monkeypatch.setattr(
        pud_mod.LiveExecutionSession, "add_worker_if_enabled",
        lambda self, key, worker, *, bus=None: None,
    )

    from vike_trader_app.exec.arm_spec import ExecArmSpec

    spec = ExecArmSpec(
        venue="bybit", environment="DEMO", product="spot",
        symbol="BTCUSDT", leverage=1.0,
        symbols=("BTCUSDT", "ETHUSDT"),
    )

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec(spec=spec)
        assert ok is True, "_maybe_start_live_exec returned False"

        # _register_basket_worker must have been called for ETHUSDT (the extra symbol)
        # and its now_ms must be wall-clock accurate.
        assert len(captured_now_ms) >= 1, (
            "make_bybit_run_core was not called for the extra basket symbol — "
            "check _register_basket_worker bybit spot branch"
        )

        before = int(time.time() * 1000) - 5_000
        after = int(time.time() * 1000) + 5_000

        for nm in captured_now_ms:
            ts = nm()
            assert ts != 0, (
                f"now_ms() returned 0 — the CRITICAL bug is still present. "
                f"Every basket WS worker must use wall-clock now_ms."
            )
            assert before <= ts <= after, (
                f"now_ms() returned {ts} which is not within ±5s of wall-clock "
                f"({before}..{after}). Expected int(time.time()*1000)."
            )
    finally:
        win.shutdown()
