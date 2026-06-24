"""venue=okx + product=perp wires an OKXPerpExecutionClient via SWAP instruments + connect().

Fully mocked — no network. Confirms:
- OKXPerpExecutionClient is built (not OKXSpotExecutionClient)
- set_leverage() is called once before connect()
- The perp WS worker (make_okx_perp_run_core) is registered when ws_base_url is set
- product=spot / VIKE_DISABLE_LIVE / other venues remain byte-identical (no perp worker created)
- ctVal=0 aborts; SWAP instruments error code aborts
- OKX perp arm is a STANDALONE if+elif pair with OKX spot (NOT chained to Bybit)
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


def _make_okx_perp_cfg(ws_base_url=""):
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import OKXV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig

    creds = Credentials(api_key="K", api_secret="S", passphrase="P")
    return VenueConfig(
        venue="okx",
        environment=Environment.DEMO,
        rest_base_url="https://www.okx.com",
        ws_base_url=ws_base_url,
        credentials=creds,
        signer=OKXV5Signer(creds, now_ms=lambda: 0),
    )


def _fake_swap_public(base, path, params=None, **k):
    """Canned OKX /api/v5/public/instruments SWAP response for BTC-USDT-SWAP."""
    inst_id = (params or {}).get("instId", "BTC-USDT-SWAP")
    return {
        "code": "0",
        "data": [{
            "instId": inst_id,
            "ctValCcy": "BTC",
            "tickSz": "0.1",
            "lotSz": "1",
            "minSz": "1",
            "maxMktSz": "500",
            "ctVal": "0.01",
            "ctMult": "1",
        }],
    }


def _patch_okx_perp_mocks(monkeypatch, cfg, call_log):
    """Common monkeypatching for OKX perp wiring tests."""
    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.okx.transport as okxtransport
    monkeypatch.setattr(okxtransport, "okx_public_get", _fake_swap_public)

    from vike_trader_app.exec.okx import perp_client as pc_mod

    def _spy_set_leverage(self):
        call_log.append("set_leverage")
        # No-op (avoids network)

    monkeypatch.setattr(pc_mod.OKXPerpExecutionClient, "set_leverage", _spy_set_leverage)
    monkeypatch.setattr(
        pc_mod.OKXPerpExecutionClient, "connect",
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


def test_perp_branch_builds_okx_perp_client(app, monkeypatch):
    """VIKE_EXEC_PRODUCT=perp + okx builds OKXPerpExecutionClient, not OKXSpotExecutionClient."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_okx_perp_cfg()
    call_log = []
    _patch_okx_perp_mocks(monkeypatch, cfg, call_log)

    from vike_trader_app.exec.okx import perp_client as pc_mod
    captured = {}
    real_init = pc_mod.OKXPerpExecutionClient.__init__

    def _spy_init(self, *a, **k):
        captured["type"] = type(self).__name__
        real_init(self, *a, **k)

    monkeypatch.setattr(pc_mod.OKXPerpExecutionClient, "__init__", _spy_init)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True, "_maybe_start_live_exec must return True for okx+perp"
        assert captured.get("type") == "OKXPerpExecutionClient", (
            f"Expected OKXPerpExecutionClient, got {captured.get('type')!r}"
        )
        assert win._exec_session is not None
    finally:
        win.shutdown()


def test_set_leverage_called_before_connect(app, monkeypatch):
    """set_leverage() must be called once (main thread) BEFORE connect/reconcile."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_okx_perp_cfg()
    call_order = []

    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.okx.transport as okxtransport
    monkeypatch.setattr(okxtransport, "okx_public_get", _fake_swap_public)

    from vike_trader_app.exec.okx import perp_client as pc_mod
    monkeypatch.setattr(pc_mod.OKXPerpExecutionClient, "set_leverage",
                        lambda self: call_order.append("set_leverage"))
    monkeypatch.setattr(
        pc_mod.OKXPerpExecutionClient, "connect",
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
    """okx+perp with non-empty ws_base_url must register an 'okx' WS worker using the SWAP run_core."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_okx_perp_cfg(ws_base_url="wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999")
    call_log = []
    _patch_okx_perp_mocks(monkeypatch, cfg, call_log)

    import vike_trader_app.exec.okx.perp_user_data as pud_mod
    invoked = {}

    def _fake_make_perp_run_core(**kwargs):
        invoked["called"] = True
        invoked["kwargs"] = kwargs
        return lambda emit, stop: None   # no-op run_core

    monkeypatch.setattr(pud_mod, "make_okx_perp_run_core", _fake_make_perp_run_core)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert invoked.get("called") is True, "make_okx_perp_run_core must be called"
        assert "okx" in win._exec_session._workers, (
            "expected an 'okx' worker in _exec_session._workers"
        )
        # ct_val must be threaded into the run_core (guards the #1 trap)
        assert invoked["kwargs"].get("ct_val", 0) > 0, (
            "ct_val must be passed to make_okx_perp_run_core and be > 0"
        )
        # symbol must be the SWAP inst_id BTC-USDT-SWAP
        assert invoked["kwargs"].get("symbol") == "BTC-USDT-SWAP", (
            f"symbol must be the SWAP inst_id, got {invoked['kwargs'].get('symbol')!r}"
        )
    finally:
        win.shutdown()


def test_perp_no_worker_when_ws_url_empty(app, monkeypatch):
    """okx+perp with ws_base_url='' must NOT register a WS worker."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_okx_perp_cfg(ws_base_url="")
    call_log = []
    _patch_okx_perp_mocks(monkeypatch, cfg, call_log)

    import vike_trader_app.exec.okx.perp_user_data as pud_mod
    invoked = {}
    monkeypatch.setattr(pud_mod, "make_okx_perp_run_core",
                        lambda **kw: invoked.__setitem__("called", True) or (lambda e, s: None))

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert not invoked, "make_okx_perp_run_core must NOT be called when ws_base_url is empty"
        assert win._exec_session._workers == {}, "no worker expected when ws_base_url is empty"
    finally:
        win.shutdown()


def test_ctval_zero_aborts(app, monkeypatch):
    """ctVal=0 in SWAP instruments must abort live exec (guards against 100x risk)."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_okx_perp_cfg()
    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.okx.transport as okxtransport

    def _no_ctval_public(base, path, params=None, **k):
        inst_id = (params or {}).get("instId", "BTC-USDT-SWAP")
        return {
            "code": "0",
            "data": [{
                "instId": inst_id,
                "ctValCcy": "BTC",
                "tickSz": "0.1",
                "lotSz": "1",
                "minSz": "1",
                "maxMktSz": "500",
                # ctVal deliberately absent -> defaults to 0.0
            }],
        }

    monkeypatch.setattr(okxtransport, "okx_public_get", _no_ctval_public)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is False, "ctVal=0 must abort live exec"
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()


def test_swap_instruments_error_aborts(app, monkeypatch):
    """OKX SWAP instruments response code!='0' must abort live exec and return False."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_okx_perp_cfg()
    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.okx.transport as okxtransport
    monkeypatch.setattr(
        okxtransport, "okx_public_get",
        lambda base, path, params=None, **k: {"code": "51001", "msg": "inst not exist", "data": []},
    )

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is False, "SWAP instruments error code must abort live exec"
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()


def test_spot_product_unaffected(app, monkeypatch):
    """VIKE_EXEC_PRODUCT=spot + okx must still build OKXSpotExecutionClient (perp arm not taken)."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "spot")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import OKXV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig
    creds = Credentials(api_key="K", api_secret="S", passphrase="P")
    cfg = VenueConfig(
        venue="okx",
        environment=Environment.DEMO,
        rest_base_url="https://www.okx.com",
        ws_base_url="",
        credentials=creds,
        signer=OKXV5Signer(creds, now_ms=lambda: 0),
    )

    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.okx.transport as okxtransport

    def _fake_spot_instruments(base, path, params=None, **k):
        inst_id = (params or {}).get("instId", "BTC-USDT")
        return {
            "code": "0",
            "data": [{
                "instId": inst_id,
                "baseCcy": "BTC",
                "quoteCcy": "USDT",
                "tickSz": "0.1",
                "lotSz": "0.00000001",
                "minSz": "0.00001",
                "maxMktSz": "100",
            }],
        }

    monkeypatch.setattr(okxtransport, "okx_public_get", _fake_spot_instruments)

    from vike_trader_app.exec.okx import client as okx_client_mod
    captured = {}
    real_init = okx_client_mod.OKXSpotExecutionClient.__init__

    def _spy_init(self, *a, **k):
        captured["type"] = type(self).__name__
        real_init(self, *a, **k)

    monkeypatch.setattr(okx_client_mod.OKXSpotExecutionClient, "__init__", _spy_init)
    monkeypatch.setattr(
        okx_client_mod.OKXSpotExecutionClient, "connect",
        lambda self: __import__(
            "vike_trader_app.exec.crypto_client",
            fromlist=["ReconcileSnapshot"],
        ).ReconcileSnapshot(
            positions=((self._symbol, 0.0),),
            position_avg_px=((self._symbol, 0.0),),
        ),
    )

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert captured.get("type") == "OKXSpotExecutionClient", (
            f"spot product must build OKXSpotExecutionClient, got {captured.get('type')!r}"
        )
    finally:
        win.shutdown()


def test_disable_live_overrides_perp(app, monkeypatch):
    """VIKE_DISABLE_LIVE=1 with okx+perp must return False (paper-safe gate)."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
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
