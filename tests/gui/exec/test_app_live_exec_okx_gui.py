"""venue=okx wires an OKXSpotExecutionClient via okx_public_get (browser-UA) + connect(), reusing
LiveOmsHub. Fully mocked — no network. Blank venue / VIKE_DISABLE_LIVE still -> no session.
The binance-transport regression guard ensures the OKX branch NEVER routes through get_public_json
(Cloudflare-UA would be blocked by OKX; browser-UA must come from okx.transport)."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _fake_okx_instruments(base_url, path, params=None, **k):
    """Canned OKX /api/v5/public/instruments response for BTC-USDT."""
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


def _make_cfg():
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import OKXV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig
    creds = Credentials(api_key="K", api_secret="S", passphrase="P")
    return VenueConfig(
        venue="okx",
        environment=Environment.DEMO,
        rest_base_url="https://www.okx.com",
        ws_base_url="",
        credentials=creds,
        signer=OKXV5Signer(creds, now_ms=lambda: 0),
    )


def test_okx_branch_builds_okx_client(app, monkeypatch):
    """VIKE_EXEC_VENUE=okx builds OKXSpotExecutionClient and creates an exec session."""
    import vike_trader_app.exec.okx.transport as okxtransport
    from vike_trader_app.exec import venue_config as vc
    from vike_trader_app.exec.okx import client as okx_client_mod

    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_cfg()
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    # Spy on OKXSpotExecutionClient.__init__ to confirm it's called.
    captured = {}
    real_init = okx_client_mod.OKXSpotExecutionClient.__init__

    def _spy_init(self, *a, **k):
        captured["built"] = True
        real_init(self, *a, **k)

    monkeypatch.setattr(okx_client_mod.OKXSpotExecutionClient, "__init__", _spy_init)
    # NOTE: the OKX client's self._symbol is BTC-USDT (inst_id), but LiveOmsHub.symbol is BTCUSDT
    # (the user's symbol). apply_snapshot asserts sym == hub.symbol, so we return the hub's symbol.
    monkeypatch.setattr(
        okx_client_mod.OKXSpotExecutionClient, "connect",
        lambda self: __import__("vike_trader_app.exec.crypto_client",
                                fromlist=["ReconcileSnapshot"]).ReconcileSnapshot(
            positions=(("BTCUSDT", 0.0),),
            position_avg_px=(("BTCUSDT", 0.0),),
        ),
    )

    # NOTE: this relies on the LAZY import of okx_public_get inside _maybe_start_live_exec;
    # if the import is ever hoisted to module-top, this monkeypatch will silently stop working.
    monkeypatch.setattr(okxtransport, "okx_public_get", _fake_okx_instruments)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert captured.get("built") is True
        assert win._exec_session is not None
    finally:
        win.shutdown()


def test_okx_instruments_code_nonzero_aborts(app, monkeypatch):
    """OKX instruments response code!='0' must abort live exec and return False (envelope guard)."""
    import vike_trader_app.exec.okx.transport as okxtransport
    from vike_trader_app.exec import venue_config as vc

    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_cfg()
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)
    monkeypatch.setattr(
        okxtransport, "okx_public_get",
        lambda base, path, params=None, **k: {"code": "51001", "msg": "bad", "data": []},
    )

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is False, "code!=0 instruments response must abort live exec"
        assert getattr(win, "_exec_session", None) is None
    finally:
        win.shutdown()


def test_okx_public_fetch_uses_okx_getter_not_binance(app, monkeypatch):
    """The OKX branch must NOT call binance.transport.get_public_json (Cloudflare-UA regression guard).

    If the OKX branch accidentally routes through the Binance getter it would be blocked by OKX
    (wrong User-Agent). This test poisons the Binance getter with AssertionError and confirms the
    OKX branch still works via its own okx_public_get (browser UA + x-simulated-trading).

    NOTE: relies on the LAZY import of okx_public_get inside _maybe_start_live_exec;
    if that import is ever hoisted, update this test's monkeypatch target accordingly.
    """
    import vike_trader_app.exec.binance.transport as btransport
    import vike_trader_app.exec.okx.transport as okxtransport
    from vike_trader_app.exec import venue_config as vc
    from vike_trader_app.exec.okx import client as okx_client_mod

    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_cfg()
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    # Poison the Binance getter — must NOT be called.
    monkeypatch.setattr(
        btransport, "get_public_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("OKX must not use binance get_public_json")),
    )

    # Provide a working OKX public getter.
    monkeypatch.setattr(okxtransport, "okx_public_get", _fake_okx_instruments)

    # NOTE: return hub symbol (BTCUSDT) not client inst_id (BTC-USDT); apply_snapshot asserts equality.
    monkeypatch.setattr(
        okx_client_mod.OKXSpotExecutionClient, "connect",
        lambda self: __import__("vike_trader_app.exec.crypto_client",
                                fromlist=["ReconcileSnapshot"]).ReconcileSnapshot(
            positions=(("BTCUSDT", 0.0),),
            position_avg_px=(("BTCUSDT", 0.0),),
        ),
    )

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True, "OKX branch failed even though okx_public_get was provided (used binance getter?)"
    finally:
        win.shutdown()


def test_disable_live_overrides_okx(app, monkeypatch):
    """VIKE_DISABLE_LIVE=1 must suppress all live exec regardless of venue."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        assert win._maybe_start_live_exec() is False
    finally:
        win.shutdown()
