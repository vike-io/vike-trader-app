"""venue=deribit wires a DeribitExecutionClient via fetch_option_instruments +
DeribitOrderTransport.connect() (MAIN thread) + make_deribit_run_core worker.
Fully mocked — no network. VIKE_DISABLE_LIVE=1 -> no session guard.

Safety bar:
- DeribitOrderTransport.connect() is monkeypatched to a no-op (NEVER a real socket).
- fetch_option_instruments is monkeypatched to return a canned dict.
- DeribitExecutionClient.connect() returns a canned empty ReconcileSnapshot.
- make_deribit_run_core is monkeypatched to a no-op.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402

_SYMBOL = "BTC-27JUN26-100000-C"
_CURRENCY = "BTC"

_FAKE_FILTERS = {
    _SYMBOL: {
        "tick_size": 0.0001, "step_size": 0.1, "min_qty": 0.1,
        "max_qty": 0.0, "min_notional": 0.0,
        "contract_size": 1.0, "base_asset": "BTC",
    }
}


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _make_deribit_cfg(ws_base_url=""):
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.venue_config import VenueConfig
    creds = Credentials(api_key="cid", api_secret="csec", passphrase="")
    return VenueConfig(
        venue="deribit",
        environment=Environment.DEMO,
        rest_base_url="https://test.deribit.com",
        ws_base_url=ws_base_url,
        credentials=creds,
        signer=None,
    )


def _patch_common(monkeypatch, cfg):
    """Apply all required monkeypatches for the deribit arm (no network)."""
    from vike_trader_app.exec import venue_config as vc
    import vike_trader_app.exec.deribit.public as deribit_public
    import vike_trader_app.exec.deribit.client as deribit_client
    import vike_trader_app.exec.deribit.transport as deribit_transport

    monkeypatch.setenv("VIKE_EXEC_VENUE", "deribit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "option")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    # Public instruments getter: return canned dict
    monkeypatch.setattr(deribit_public, "fetch_option_instruments",
                        lambda currency, base_url, **k: _FAKE_FILTERS)

    # DeribitOrderTransport.connect() -> no-op (NEVER open a real socket)
    monkeypatch.setattr(deribit_transport.DeribitOrderTransport, "connect", lambda self: None)

    # DeribitExecutionClient.connect() -> empty ReconcileSnapshot
    from vike_trader_app.exec.crypto_client import ReconcileSnapshot
    monkeypatch.setattr(deribit_client.DeribitExecutionClient, "connect",
                        lambda self: ReconcileSnapshot())


def test_deribit_arm_builds_session(app, monkeypatch):
    """Deribit arm: exec session built, hub.symbol == instrument name, signer is None."""
    cfg = _make_deribit_cfg(ws_base_url="")
    _patch_common(monkeypatch, cfg)

    win = MainWindow()
    try:
        win._symbol = _SYMBOL
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert win._exec_session is not None
        hub = win._exec_session._hub
        assert hub.symbol == _SYMBOL
        assert hub.venue == "deribit"
    finally:
        win.shutdown()


def test_deribit_arm_worker_registered_when_ws_url_set(app, monkeypatch):
    """Deribit arm: PrivateUserDataWorker registered when ws_base_url is set."""
    cfg = _make_deribit_cfg(ws_base_url="wss://test.deribit.com/ws/api/v2")
    _patch_common(monkeypatch, cfg)

    import vike_trader_app.exec.deribit.user_data as deribit_user_data
    captured_kwargs = {}

    def _spy_make_run_core(**kwargs):
        captured_kwargs.update(kwargs)
        return lambda emit, stop: None  # no-op run_core

    monkeypatch.setattr(deribit_user_data, "make_deribit_run_core", _spy_make_run_core)

    win = MainWindow()
    try:
        win._symbol = _SYMBOL
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert "deribit" in win._exec_session._workers
        assert captured_kwargs.get("currency") == _CURRENCY
        assert captured_kwargs.get("symbol") == _SYMBOL
    finally:
        win.shutdown()


def test_deribit_no_worker_when_ws_url_empty(app, monkeypatch):
    """Deribit arm: no worker when ws_base_url is empty."""
    cfg = _make_deribit_cfg(ws_base_url="")
    _patch_common(monkeypatch, cfg)

    import vike_trader_app.exec.deribit.user_data as deribit_user_data
    called = []

    def _spy_make_run_core(**kwargs):
        called.append(kwargs)
        return lambda emit, stop: None

    monkeypatch.setattr(deribit_user_data, "make_deribit_run_core", _spy_make_run_core)

    win = MainWindow()
    try:
        win._symbol = _SYMBOL
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert called == [], "make_deribit_run_core must not be called when ws_base_url is empty"
        assert win._exec_session._workers == {}
    finally:
        win.shutdown()


def test_disable_live_prevents_deribit_session(app, monkeypatch):
    """VIKE_DISABLE_LIVE=1 -> no session regardless of venue."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "deribit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "option")
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")

    win = MainWindow()
    try:
        win._symbol = _SYMBOL
        ok = win._maybe_start_live_exec()
        assert ok is False
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()


def test_deribit_invalid_symbol_aborts(app, monkeypatch):
    """Deribit arm: non-option symbol (e.g. 'BTCUSDT') -> parse_instrument_name returns None -> abort."""
    cfg = _make_deribit_cfg()
    from vike_trader_app.exec import venue_config as vc
    import vike_trader_app.exec.deribit.public as deribit_public
    import vike_trader_app.exec.deribit.transport as deribit_transport

    monkeypatch.setenv("VIKE_EXEC_VENUE", "deribit")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "option")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)
    monkeypatch.setattr(deribit_transport.DeribitOrderTransport, "connect", lambda self: None)
    monkeypatch.setattr(deribit_public, "fetch_option_instruments",
                        lambda currency, base_url, **k: _FAKE_FILTERS)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"   # NOT a valid option instrument name
        ok = win._maybe_start_live_exec()
        assert ok is False, "non-option symbol must abort deribit arm"
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()
