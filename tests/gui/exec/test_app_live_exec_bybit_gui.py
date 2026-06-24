"""venue=bybit wires a BybitSpotExecutionClient via instruments-info + connect(), reusing LiveOmsHub.
Fully mocked — no network. Blank venue / VIKE_DISABLE_LIVE still -> no session."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_bybit_branch_builds_bybit_client(app, monkeypatch):
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import BybitV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig
    import vike_trader_app.ui.app as appmod

    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    creds = Credentials(api_key="K", api_secret="S")
    cfg = VenueConfig(venue="bybit", environment=Environment.DEMO,
                      rest_base_url="https://api-demo.bybit.com", ws_base_url="",
                      credentials=creds, signer=BybitV5Signer(creds, now_ms=lambda: 0))
    monkeypatch.setattr(appmod, "_TEST_FORCE_CFG", cfg, raising=False)

    # Mock resolve_venue_config + the public instruments-info fetch + connect()/reconcile.
    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    from vike_trader_app.exec.bybit import client as bybit_client_mod
    captured = {}
    real_init = bybit_client_mod.BybitSpotExecutionClient.__init__

    def _spy_init(self, *a, **k):
        captured["built"] = True
        real_init(self, *a, **k)
    monkeypatch.setattr(bybit_client_mod.BybitSpotExecutionClient, "__init__", _spy_init)
    monkeypatch.setattr(bybit_client_mod.BybitSpotExecutionClient, "connect",
                        lambda self: __import__("vike_trader_app.exec.crypto_client",
                                                fromlist=["ReconcileSnapshot"]).ReconcileSnapshot(
                            positions=((self._symbol, 0.0),),
                            position_avg_px=((self._symbol, 0.0),)))

    # Mock the instruments-info public GET to return a canned Bybit payload.
    # NOTE: this relies on the LAZY import of get_public_json inside _maybe_start_live_exec;
    # if the import is ever hoisted to module-top, this monkeypatch will silently stop working.
    import vike_trader_app.exec.binance.transport as btransport

    def _fake_public(base_url, path, params=None):
        return {"retCode": 0, "result": {"list": [
            {"symbol": params.get("symbol"), "baseCoin": "BTC",
             "priceFilter": {"tickSize": "0.01"},
             "lotSizeFilter": {"basePrecision": "0.000001", "minOrderQty": "0.0001",
                               "maxOrderQty": "100", "minOrderAmt": "1"}}]}}
    monkeypatch.setattr(btransport, "get_public_json", _fake_public)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert captured.get("built") is True
        assert win._exec_session is not None
    finally:
        win.shutdown()


def test_mixed_case_venue_dispatches_to_bybit(app, monkeypatch):
    """VIKE_EXEC_VENUE='Bybit' (mixed case) must normalize and dispatch to BybitSpotExecutionClient,
    not fall through to the Binance branch (Fix 2: normalize venue once with .lower())."""
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import BybitV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig
    import vike_trader_app.ui.app as appmod

    monkeypatch.setenv("VIKE_EXEC_VENUE", "Bybit")   # mixed-case — the key case to test
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    creds = Credentials(api_key="K", api_secret="S")
    cfg = VenueConfig(venue="bybit", environment=Environment.DEMO,
                      rest_base_url="https://api-demo.bybit.com", ws_base_url="",
                      credentials=creds, signer=BybitV5Signer(creds, now_ms=lambda: 0))

    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    from vike_trader_app.exec.bybit import client as bybit_client_mod
    captured = {}
    real_init = bybit_client_mod.BybitSpotExecutionClient.__init__

    def _spy_init(self, *a, **k):
        captured["built"] = True
        real_init(self, *a, **k)
    monkeypatch.setattr(bybit_client_mod.BybitSpotExecutionClient, "__init__", _spy_init)
    monkeypatch.setattr(bybit_client_mod.BybitSpotExecutionClient, "connect",
                        lambda self: __import__("vike_trader_app.exec.crypto_client",
                                                fromlist=["ReconcileSnapshot"]).ReconcileSnapshot(
                            positions=((self._symbol, 0.0),),
                            position_avg_px=((self._symbol, 0.0),)))

    import vike_trader_app.exec.binance.transport as btransport

    def _fake_public(base_url, path, params=None):
        return {"retCode": 0, "result": {"list": [
            {"symbol": params.get("symbol"), "baseCoin": "BTC",
             "priceFilter": {"tickSize": "0.01"},
             "lotSizeFilter": {"basePrecision": "0.000001", "minOrderQty": "0.0001",
                               "maxOrderQty": "100", "minOrderAmt": "1"}}]}}
    monkeypatch.setattr(btransport, "get_public_json", _fake_public)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True, "mixed-case 'Bybit' venue did not start a live session"
        assert captured.get("built") is True, "BybitSpotExecutionClient was not built (Binance branch used instead)"
    finally:
        win.shutdown()


def test_bybit_retcode_nonzero_instruments_aborts(app, monkeypatch):
    """Bybit instruments-info retCode!=0 (HTTP 200 business error) must abort live exec
    and return False — not silently accept zero-filter defaults (Fix 3)."""
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import BybitV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig

    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    creds = Credentials(api_key="K", api_secret="S")
    cfg = VenueConfig(venue="bybit", environment=Environment.DEMO,
                      rest_base_url="https://api-demo.bybit.com", ws_base_url="",
                      credentials=creds, signer=BybitV5Signer(creds, now_ms=lambda: 0))

    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.binance.transport as btransport

    def _fake_public_error(base_url, path, params=None):
        # Bybit returns business errors inside HTTP 200 bodies
        return {"retCode": 10001, "retMsg": "Server error", "result": {}}
    monkeypatch.setattr(btransport, "get_public_json", _fake_public_error)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is False, "retCode!=0 instruments response must abort live exec (zero-filter guard)"
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()


def test_disable_live_overrides_bybit(app, monkeypatch):
    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        assert win._maybe_start_live_exec() is False
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# Task 7 — WS worker wiring tests
# ---------------------------------------------------------------------------

def _setup_bybit_mocks(monkeypatch, ws_base_url=""):
    """Shared mock setup for Bybit gated-live tests (no network)."""
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
    monkeypatch.setattr(bybit_client_mod.BybitSpotExecutionClient, "connect",
                        lambda self: __import__(
                            "vike_trader_app.exec.crypto_client",
                            fromlist=["ReconcileSnapshot"],
                        ).ReconcileSnapshot(
                            positions=((self._symbol, 0.0),),
                            position_avg_px=((self._symbol, 0.0),),
                        ))

    import vike_trader_app.exec.binance.transport as btransport

    def _fake_public(base_url, path, params=None):
        return {"retCode": 0, "result": {"list": [
            {"symbol": params.get("symbol"), "baseCoin": "BTC",
             "priceFilter": {"tickSize": "0.01"},
             "lotSizeFilter": {"basePrecision": "0.000001", "minOrderQty": "0.0001",
                               "maxOrderQty": "100", "minOrderAmt": "1"}}]}}

    monkeypatch.setattr(btransport, "get_public_json", _fake_public)
    return cfg


def test_bybit_ws_worker_registered_when_ws_url_set(app, monkeypatch):
    """Bybit gated session with a non-empty ws_base_url must register a 'bybit' WS worker."""
    _setup_bybit_mocks(monkeypatch, ws_base_url="wss://stream-demo.bybit.com/v5/private")

    # Patch make_bybit_run_core to return a no-op (avoids real asyncio.run/socket).
    import vike_trader_app.exec.bybit.user_data as ud_mod
    invoked = {}

    def _fake_make_run_core(**kwargs):
        invoked["called"] = True
        return lambda emit, stop: None   # no-op; QThread.run() returns immediately

    monkeypatch.setattr(ud_mod, "make_bybit_run_core", _fake_make_run_core)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert invoked.get("called") is True, "make_bybit_run_core was not called"
        assert "bybit" in win._exec_session._workers, (
            "expected a 'bybit' worker in _exec_session._workers"
        )
    finally:
        win.shutdown()   # joins worker; no-op run_core finishes immediately


def test_bybit_no_worker_when_ws_url_empty(app, monkeypatch):
    """Bybit gated session with ws_base_url='' must start NO WS worker (REST-only, paper-safe)."""
    _setup_bybit_mocks(monkeypatch, ws_base_url="")

    import vike_trader_app.exec.bybit.user_data as ud_mod
    invoked = {}
    monkeypatch.setattr(ud_mod, "make_bybit_run_core",
                        lambda **kw: invoked.__setitem__("called", True) or (lambda e, s: None))

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert not invoked, "make_bybit_run_core must NOT be called when ws_base_url is empty"
        assert win._exec_session._workers == {}, (
            "no worker expected when ws_base_url is empty"
        )
    finally:
        win.shutdown()


def test_disable_live_registers_no_bybit_worker(app, monkeypatch):
    """VIKE_DISABLE_LIVE=1 with a non-empty ws_base_url → False return, no session, no worker."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    # Don't bother patching instruments/connect — we should never reach that code.
    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is False
        assert getattr(win, "_exec_session", None) is None, (
            "no session must be created when VIKE_DISABLE_LIVE is set"
        )
    finally:
        win.shutdown()
