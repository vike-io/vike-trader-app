"""venue=bybit + product=perp wires a BybitPerpExecutionClient via linear instruments-info + connect().

Fully mocked — no network. Confirms:
- BybitPerpExecutionClient is built (not BybitSpotExecutionClient)
- set_leverage() is called once before connect()
- The perp WS worker (make_bybit_perp_run_core) is registered when ws_base_url is set
- product=spot / other venues remain byte-identical (no perp worker created)
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_perp_cfg(ws_base_url=""):
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import BybitV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig

    creds = Credentials(api_key="K", api_secret="S")
    return VenueConfig(
        venue="bybit", environment=Environment.DEMO,
        rest_base_url="https://api-demo.bybit.com",
        ws_base_url=ws_base_url,
        credentials=creds,
        signer=BybitV5Signer(creds, now_ms=lambda: 0),
    )


def _fake_perp_public(base_url, path, params=None):
    """Canned Bybit linear instruments-info response."""
    sym = (params or {}).get("symbol", "BTCUSDT")
    return {
        "retCode": 0,
        "result": {"list": [{
            "symbol": sym,
            "baseCoin": "BTC",
            "priceFilter": {"tickSize": "0.01"},
            "lotSizeFilter": {
                "qtyStep": "0.001",
                "minOrderQty": "0.001",
                "maxOrderQty": "100",
                "minNotionalValue": "5",
            },
        }]},
    }


def _patch_perp_mocks(monkeypatch, cfg, leverage_calls):
    """Common monkeypatching for perp wiring tests."""
    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.binance.transport as btransport
    monkeypatch.setattr(btransport, "get_public_json", _fake_perp_public)

    # Spy on BybitPerpExecutionClient.set_leverage and connect
    from vike_trader_app.exec.bybit import perp_client as pc_mod

    real_set_leverage = pc_mod.BybitPerpExecutionClient.set_leverage

    def _spy_set_leverage(self):
        leverage_calls.append("set_leverage")
        # No-op (don't call real — avoids network)

    monkeypatch.setattr(pc_mod.BybitPerpExecutionClient, "set_leverage", _spy_set_leverage)
    monkeypatch.setattr(
        pc_mod.BybitPerpExecutionClient, "connect",
        lambda self: __import__(
            "vike_trader_app.exec.crypto_client",
            fromlist=["ReconcileSnapshot"],
        ).ReconcileSnapshot(
            positions=((self._symbol, 0.0),),
            position_avg_px=((self._symbol, 0.0),),
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_perp_branch_builds_perp_client(app, monkeypatch):
    """VIKE_EXEC_PRODUCT=perp + bybit builds a BybitPerpExecutionClient, not BybitSpotExecutionClient."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_perp_cfg()
    leverage_calls = []
    _patch_perp_mocks(monkeypatch, cfg, leverage_calls)

    # Track which client class was instantiated
    from vike_trader_app.exec.bybit import perp_client as pc_mod
    captured = {}
    real_init = pc_mod.BybitPerpExecutionClient.__init__

    def _spy_init(self, *a, **k):
        captured["type"] = type(self).__name__
        real_init(self, *a, **k)

    monkeypatch.setattr(pc_mod.BybitPerpExecutionClient, "__init__", _spy_init)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True, "_maybe_start_live_exec must return True for bybit+perp"
        assert captured.get("type") == "BybitPerpExecutionClient", (
            f"Expected BybitPerpExecutionClient, got {captured.get('type')!r}"
        )
        assert win._exec_session is not None
    finally:
        win.shutdown()


def test_perp_set_leverage_called_before_connect(app, monkeypatch):
    """set_leverage() must be called once (on the main thread, before connect/reconcile)."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_perp_cfg()
    call_order = []

    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.binance.transport as btransport
    monkeypatch.setattr(btransport, "get_public_json", _fake_perp_public)

    from vike_trader_app.exec.bybit import perp_client as pc_mod
    monkeypatch.setattr(pc_mod.BybitPerpExecutionClient, "set_leverage",
                        lambda self: call_order.append("set_leverage"))
    monkeypatch.setattr(
        pc_mod.BybitPerpExecutionClient, "connect",
        lambda self: (
            call_order.append("connect"),
            __import__(
                "vike_trader_app.exec.crypto_client",
                fromlist=["ReconcileSnapshot"],
            ).ReconcileSnapshot(
                positions=((self._symbol, 0.0),),
                position_avg_px=((self._symbol, 0.0),),
            ),
        )[1],
    )

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert call_order.count("set_leverage") == 1, (
            f"set_leverage must be called exactly once, got call_order={call_order}"
        )
        assert call_order.count("connect") == 1, "connect() must be called exactly once"
        assert call_order.index("set_leverage") < call_order.index("connect"), (
            "set_leverage must be called BEFORE connect() — "
            f"got order: {call_order}"
        )
    finally:
        win.shutdown()


def test_perp_ws_worker_registered_when_ws_url_set(app, monkeypatch):
    """bybit+perp with non-empty ws_base_url must register a 'bybit' WS worker using the perp run_core."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_perp_cfg(ws_base_url="wss://stream-demo.bybit.com/v5/private")
    leverage_calls = []
    _patch_perp_mocks(monkeypatch, cfg, leverage_calls)

    import vike_trader_app.exec.bybit.perp_user_data as pud_mod
    invoked = {}

    def _fake_make_perp_run_core(**kwargs):
        invoked["called"] = True
        return lambda emit, stop: None   # no-op run_core

    monkeypatch.setattr(pud_mod, "make_bybit_perp_run_core", _fake_make_perp_run_core)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert invoked.get("called") is True, "make_bybit_perp_run_core must be called"
        assert "bybit" in win._exec_session._workers, (
            "expected a 'bybit' worker in _exec_session._workers"
        )
    finally:
        win.shutdown()


def test_perp_no_worker_when_ws_url_empty(app, monkeypatch):
    """bybit+perp with ws_base_url='' must not register a WS worker."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_perp_cfg(ws_base_url="")
    leverage_calls = []
    _patch_perp_mocks(monkeypatch, cfg, leverage_calls)

    import vike_trader_app.exec.bybit.perp_user_data as pud_mod
    invoked = {}
    monkeypatch.setattr(pud_mod, "make_bybit_perp_run_core",
                        lambda **kw: invoked.__setitem__("called", True) or (lambda e, s: None))

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert not invoked, "make_bybit_perp_run_core must NOT be called when ws_base_url is empty"
        assert win._exec_session._workers == {}, "no worker expected when ws_base_url is empty"
    finally:
        win.shutdown()


def test_perp_retcode_nonzero_instruments_aborts(app, monkeypatch):
    """bybit+perp linear instruments-info retCode!=0 must abort live exec and return False."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_perp_cfg()
    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.binance.transport as btransport
    monkeypatch.setattr(btransport, "get_public_json",
                        lambda *a, **k: {"retCode": 10001, "retMsg": "Server error", "result": {}})

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is False, "retCode!=0 must abort live exec for perp product"
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()


def test_spot_product_unaffected_by_perp_branch(app, monkeypatch):
    """VIKE_EXEC_PRODUCT=spot (default) must still build BybitSpotExecutionClient — perp branch not taken."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "spot")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import BybitV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig
    creds = Credentials(api_key="K", api_secret="S")
    cfg = VenueConfig(
        venue="bybit", environment=Environment.DEMO,
        rest_base_url="https://api-demo.bybit.com", ws_base_url="",
        credentials=creds, signer=BybitV5Signer(creds, now_ms=lambda: 0),
    )

    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.binance.transport as btransport

    def _fake_spot_public(base_url, path, params=None):
        sym = (params or {}).get("symbol", "BTCUSDT")
        return {"retCode": 0, "result": {"list": [
            {"symbol": sym, "baseCoin": "BTC",
             "priceFilter": {"tickSize": "0.01"},
             "lotSizeFilter": {"basePrecision": "0.000001", "minOrderQty": "0.0001",
                               "maxOrderQty": "100", "minOrderAmt": "1"}}]}}

    monkeypatch.setattr(btransport, "get_public_json", _fake_spot_public)

    from vike_trader_app.exec.bybit import client as bybit_client_mod
    captured = {}
    real_init = bybit_client_mod.BybitSpotExecutionClient.__init__

    def _spy_init(self, *a, **k):
        captured["type"] = type(self).__name__
        real_init(self, *a, **k)

    monkeypatch.setattr(bybit_client_mod.BybitSpotExecutionClient, "__init__", _spy_init)
    monkeypatch.setattr(bybit_client_mod.BybitSpotExecutionClient, "connect",
                        lambda self: __import__(
                            "vike_trader_app.exec.crypto_client",
                            fromlist=["ReconcileSnapshot"],
                        ).ReconcileSnapshot(
                            positions=((self._symbol, 0.0),),
                            position_avg_px=((self._symbol, 0.0),),
                        ))

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        # spot product must build BybitSpotExecutionClient (not BybitPerpExecutionClient)
        assert captured.get("type") == "BybitSpotExecutionClient", (
            f"spot product must build BybitSpotExecutionClient, got {captured.get('type')!r}"
        )
    finally:
        win.shutdown()


def test_disable_live_overrides_perp(app, monkeypatch):
    """VIKE_DISABLE_LIVE=1 with product=perp must return False (paper-safe gate)."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")

    win = MainWindow()
    try:
        ok = win._maybe_start_live_exec()
        assert ok is False
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()
