"""venue=binance wires a BinanceSpotExecutionClient via get_public_json + connect(), reusing
LiveOmsHub. Fully mocked — no network. Blank venue / VIKE_DISABLE_LIVE still -> no session.
Task 5: Binance WS-API private fill worker guard tests (plain BTCUSDT, NO passphrase)."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _fake_binance_exchange_info(base_url, path, params=None, **k):
    """Canned Binance /api/v3/exchangeInfo response for BTCUSDT."""
    symbol = (params or {}).get("symbol", "BTCUSDT")
    return {
        "symbols": [{
            "symbol": symbol,
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001", "maxQty": "9000.0"},
                {"filterType": "NOTIONAL", "minNotional": "5.0"},
            ],
        }],
    }


def _make_cfg(ws_base_url=""):
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import BinanceHmacSigner
    from vike_trader_app.exec.venue_config import VenueConfig
    creds = Credentials(api_key="K", api_secret="S")   # NO passphrase — Binance doesn't use one
    return VenueConfig(
        venue="binance",
        environment=Environment.DEMO,
        rest_base_url="https://demo-api.binance.com",
        ws_base_url=ws_base_url,
        credentials=creds,
        signer=BinanceHmacSigner(creds, now_ms=lambda: 0),
    )


def _fake_connect_snap(symbol="BTCUSDT"):
    """Return a connect() lambda for BinanceSpotExecutionClient that returns a ReconcileSnapshot."""
    from vike_trader_app.exec.binance import client as binance_client_mod

    def _connect(self):
        return __import__(
            "vike_trader_app.exec.crypto_client", fromlist=["ReconcileSnapshot"]
        ).ReconcileSnapshot(
            positions=((symbol, 0.0),),
            position_avg_px=((symbol, 0.0),),
        )

    return _connect


# ---------------------------------------------------------------------------
# Task 5: Binance worker-start guard tests
# ---------------------------------------------------------------------------

def test_binance_ws_worker_registered_when_ws_url_set(app, monkeypatch):
    """Binance guard: worker registered + symbol='BTCUSDT' (plain) + NO passphrase kwarg when ws_base_url set."""
    import vike_trader_app.exec.binance.transport as btransport
    from vike_trader_app.exec import venue_config as vc
    from vike_trader_app.exec.binance import client as binance_client_mod
    import vike_trader_app.exec.binance.user_data as binance_user_data

    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_cfg(ws_base_url="wss://ws-api.binance.com/ws-api/v3")
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)
    monkeypatch.setattr(btransport, "get_public_json", _fake_binance_exchange_info)
    monkeypatch.setattr(binance_client_mod.BinanceSpotExecutionClient, "connect",
                        _fake_connect_snap("BTCUSDT"))

    # Spy on make_binance_run_core to capture kwargs and return a no-op run_core.
    captured_kwargs = {}

    def _spy_make_run_core(**kwargs):
        captured_kwargs.update(kwargs)
        return lambda emit, stop: None  # no-op run_core

    monkeypatch.setattr(binance_user_data, "make_binance_run_core", _spy_make_run_core)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        # Worker must be registered under 'binance'
        assert "binance" in win._exec_session._workers
        # Symbol must be the plain compact form (BTCUSDT) — NOT dashed, NOT translated
        assert captured_kwargs.get("symbol") == "BTCUSDT"
        # NO passphrase — Binance has none; assert it was NOT passed
        assert "passphrase" not in captured_kwargs, (
            "Binance make_binance_run_core must NOT receive a passphrase kwarg"
        )
    finally:
        win.shutdown()


def test_binance_no_worker_when_ws_url_empty(app, monkeypatch):
    """Binance guard: no worker when ws_base_url is empty (REST-only, paper-safe)."""
    import vike_trader_app.exec.binance.transport as btransport
    from vike_trader_app.exec import venue_config as vc
    from vike_trader_app.exec.binance import client as binance_client_mod
    import vike_trader_app.exec.binance.user_data as binance_user_data

    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_cfg(ws_base_url="")   # empty — no worker should start
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)
    monkeypatch.setattr(btransport, "get_public_json", _fake_binance_exchange_info)
    monkeypatch.setattr(binance_client_mod.BinanceSpotExecutionClient, "connect",
                        _fake_connect_snap("BTCUSDT"))

    called = []

    def _spy_make_run_core(**kwargs):
        called.append(kwargs)
        return lambda emit, stop: None

    monkeypatch.setattr(binance_user_data, "make_binance_run_core", _spy_make_run_core)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        # make_binance_run_core must NOT be called when ws_base_url is empty
        assert called == [], "make_binance_run_core must not be called when ws_base_url is empty"
        # No worker in session
        assert win._exec_session._workers == {}
    finally:
        win.shutdown()


def test_disable_live_registers_no_binance_worker(app, monkeypatch):
    """VIKE_DISABLE_LIVE=1 + non-empty ws_base_url -> returns False, no exec session, no worker."""
    import vike_trader_app.exec.binance.user_data as binance_user_data

    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")

    called = []

    def _spy_make_run_core(**kwargs):
        called.append(kwargs)
        return lambda emit, stop: None

    monkeypatch.setattr(binance_user_data, "make_binance_run_core", _spy_make_run_core)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is False, "VIKE_DISABLE_LIVE must suppress live exec"
        assert getattr(win, "_exec_session", None) is None, "no exec session when VIKE_DISABLE_LIVE=1"
        assert called == [], "make_binance_run_core must not be called when VIKE_DISABLE_LIVE=1"
    finally:
        win.shutdown()


def test_other_venue_arm_is_noop_for_binance_branch(app, monkeypatch):
    """Regression: with VIKE_EXEC_VENUE=bybit, the Binance arm must be a no-op (make_binance_run_core never called)."""
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import BybitV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig
    import vike_trader_app.exec.binance.transport as btransport
    from vike_trader_app.exec import venue_config as vc
    from vike_trader_app.exec.bybit import client as bybit_client_mod
    import vike_trader_app.exec.binance.user_data as binance_user_data

    monkeypatch.setenv("VIKE_EXEC_VENUE", "bybit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    creds = Credentials(api_key="K", api_secret="S")
    cfg = VenueConfig(
        venue="bybit", environment=Environment.DEMO,
        rest_base_url="https://api-demo.bybit.com",
        ws_base_url="wss://stream-demo.bybit.com/v5/private",
        credentials=creds,
        signer=BybitV5Signer(creds, now_ms=lambda: 0),
    )
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    def _fake_public_bybit(base_url, path, params=None):
        return {"retCode": 0, "result": {"list": [
            {"symbol": (params or {}).get("symbol", "BTCUSDT"), "baseCoin": "BTC",
             "priceFilter": {"tickSize": "0.01"},
             "lotSizeFilter": {"basePrecision": "0.000001", "minOrderQty": "0.0001",
                               "maxOrderQty": "100", "minOrderAmt": "1"}}]}}

    monkeypatch.setattr(btransport, "get_public_json", _fake_public_bybit)
    monkeypatch.setattr(
        bybit_client_mod.BybitSpotExecutionClient, "connect",
        lambda self: __import__("vike_trader_app.exec.crypto_client",
                                fromlist=["ReconcileSnapshot"]).ReconcileSnapshot(
            positions=((self._symbol, 0.0),),
            position_avg_px=((self._symbol, 0.0),),
        ),
    )

    # Also patch make_bybit_run_core so the bybit worker guard doesn't do real IO
    import vike_trader_app.exec.bybit.user_data as bybit_user_data
    monkeypatch.setattr(bybit_user_data, "make_bybit_run_core",
                        lambda **kw: (lambda emit, stop: None))

    # Spy on make_binance_run_core — must NOT be called when venue=bybit
    called = []

    def _spy_make_run_core(**kwargs):
        called.append(kwargs)
        return lambda emit, stop: None

    monkeypatch.setattr(binance_user_data, "make_binance_run_core", _spy_make_run_core)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert called == [], (
            "make_binance_run_core must NOT be called when venue='bybit' (Binance arm gated on venue=='binance')"
        )
    finally:
        win.shutdown()
