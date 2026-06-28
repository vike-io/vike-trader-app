"""Basket arm — N hubs sharing ONE Account under LiveExecutionSession.hubs (A2d Task 3).

Tests:
- 2-symbol bybit spot basket: sess.hubs has 2 entries, both share the SAME Account object,
  sess.hub is the primary hub, shutdown() tears down both.
- 1-symbol path is unchanged (one hub, sess.hub is it, sess.hubs has one entry).
- LiveExecutionSession.hubs property is populated correctly.
- shutdown() tears down ALL hubs (not just the primary).

No network — all venue calls are monkeypatched.
"""

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
# Shared mock helpers
# ---------------------------------------------------------------------------

def _setup_bybit_spot_mocks(monkeypatch, ws_base_url=""):
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


# ---------------------------------------------------------------------------
# Test: single-symbol path is unchanged
# ---------------------------------------------------------------------------

def test_single_symbol_path_unchanged(app, monkeypatch):
    """N=1: one hub, sess.hub is it, sess.hubs has exactly one entry."""
    _setup_bybit_spot_mocks(monkeypatch)

    from vike_trader_app.exec.arm_spec import ExecArmSpec

    spec = ExecArmSpec(
        venue="bybit", environment="DEMO", product="spot",
        symbol="BTCUSDT", leverage=1.0,
        # symbols unset — defaults to () → all_symbols = ("BTCUSDT",)
    )

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec(spec=spec)
        assert ok is True
        sess = win._exec_session
        assert sess is not None
        # single-symbol invariants
        assert sess.hub is not None
        assert sess.hub.symbol == "BTCUSDT"
        assert len(sess.hubs) == 1
        assert "BTCUSDT" in sess.hubs
        assert sess.hubs["BTCUSDT"] is sess.hub
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# Test: 2-symbol basket builds 2 hubs sharing ONE Account
# ---------------------------------------------------------------------------

def test_basket_arm_two_symbols_bybit_spot(app, monkeypatch):
    """N=2 bybit spot: 2 hubs in sess.hubs, same Account object, sess.hub is primary."""
    _setup_bybit_spot_mocks(monkeypatch)

    from vike_trader_app.exec.arm_spec import ExecArmSpec

    spec = ExecArmSpec(
        venue="bybit", environment="DEMO", product="spot",
        symbol="BTCUSDT", leverage=1.0,
        symbols=("BTCUSDT", "ETHUSDT"),   # explicit basket
    )

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec(spec=spec)
        assert ok is True, "_maybe_start_live_exec returned False for basket spec"
        sess = win._exec_session
        assert sess is not None

        # Both symbols present
        assert "BTCUSDT" in sess.hubs, "BTCUSDT hub missing from sess.hubs"
        assert "ETHUSDT" in sess.hubs, "ETHUSDT hub missing from sess.hubs"
        assert len(sess.hubs) == 2

        # Primary hub is the first symbol
        assert sess.hub is sess.hubs["BTCUSDT"], "sess.hub must be the primary (BTCUSDT) hub"

        # Shared Account — same object identity
        btc_account = sess.hubs["BTCUSDT"].account
        eth_account = sess.hubs["ETHUSDT"].account
        assert btc_account is eth_account, (
            "Both hubs must share the SAME Account object "
            f"(BTCUSDT={id(btc_account):#x}, ETHUSDT={id(eth_account):#x})"
        )

        # Symbols match
        assert sess.hubs["BTCUSDT"].symbol == "BTCUSDT"
        assert sess.hubs["ETHUSDT"].symbol == "ETHUSDT"
    finally:
        win.shutdown()


# ---------------------------------------------------------------------------
# Test: shutdown() tears down ALL hubs
# ---------------------------------------------------------------------------

def test_basket_shutdown_tears_down_all_hubs(app, monkeypatch):
    """shutdown() must call hub.shutdown() on EVERY hub in the basket, not just the primary."""
    _setup_bybit_spot_mocks(monkeypatch)

    from vike_trader_app.exec.arm_spec import ExecArmSpec

    spec = ExecArmSpec(
        venue="bybit", environment="DEMO", product="spot",
        symbol="BTCUSDT", leverage=1.0,
        symbols=("BTCUSDT", "ETHUSDT"),
    )

    win = MainWindow()
    try:
        ok = win._maybe_start_live_exec(spec=spec)
        assert ok is True
        sess = win._exec_session

        # Track which hubs were shut down
        shutdown_calls = []
        for sym, hub in list(sess.hubs.items()):
            _orig = hub.shutdown

            def _patched(s=sym, orig=_orig):
                shutdown_calls.append(s)
                orig()

            hub.shutdown = _patched

        # Trigger shutdown via the session directly
        sess.shutdown()

        assert set(shutdown_calls) == {"BTCUSDT", "ETHUSDT"}, (
            f"Expected both hubs to be shut down; got: {shutdown_calls}"
        )
        assert sess.hub is None, "sess.hub must be None after shutdown()"
    finally:
        win.shutdown()   # safe to call again (session already cleared)


# ---------------------------------------------------------------------------
# Test: hubs property works on single-hub session (LiveExecutionSession unit)
# ---------------------------------------------------------------------------

def test_live_execution_session_hubs_single():
    """LiveExecutionSession.hubs has one entry for single-hub construction."""
    from vike_trader_app.ui.private_user_data import LiveExecutionSession

    class _FakeHub:
        symbol = "BTCUSDT"

        def shutdown(self):
            pass

    hub = _FakeHub()
    sess = LiveExecutionSession(hub)
    assert sess.hub is hub
    assert "BTCUSDT" in sess.hubs
    assert sess.hubs["BTCUSDT"] is hub
    assert len(sess.hubs) == 1


def test_live_execution_session_hubs_basket():
    """LiveExecutionSession(hub, hubs={…}) exposes full dict + primary via .hub."""
    from vike_trader_app.ui.private_user_data import LiveExecutionSession

    class _FakeHub:
        def __init__(self, sym):
            self.symbol = sym

        def shutdown(self):
            pass

    hub_btc = _FakeHub("BTCUSDT")
    hub_eth = _FakeHub("ETHUSDT")
    hubs = {"BTCUSDT": hub_btc, "ETHUSDT": hub_eth}
    sess = LiveExecutionSession(hub_btc, hubs=hubs)

    assert sess.hub is hub_btc
    assert sess.hubs == hubs
    assert len(sess.hubs) == 2


def test_live_execution_session_shutdown_all_hubs():
    """shutdown() tears down ALL hubs in the basket, not just the primary."""
    from vike_trader_app.ui.private_user_data import LiveExecutionSession

    class _FakeHub:
        def __init__(self, sym):
            self.symbol = sym
            self.shutdown_called = False

        def shutdown(self):
            self.shutdown_called = True

    hub_btc = _FakeHub("BTCUSDT")
    hub_eth = _FakeHub("ETHUSDT")
    hubs = {"BTCUSDT": hub_btc, "ETHUSDT": hub_eth}
    sess = LiveExecutionSession(hub_btc, hubs=hubs)
    sess.shutdown()

    assert hub_btc.shutdown_called, "primary hub must be shut down"
    assert hub_eth.shutdown_called, "secondary hub must be shut down"
    assert sess.hub is None
    assert sess.hubs == {}
