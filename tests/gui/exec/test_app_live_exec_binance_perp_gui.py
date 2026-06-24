"""venue=binance + product=perp wires a BinancePerpExecutionClient via fapi exchangeInfo + connect().

Fully mocked — no network. Confirms:
- BinancePerpExecutionClient is built (not BinanceSpotExecutionClient)
- set_leverage() is called once before connect()
- The perp WS worker (make_binance_perp_run_core) is registered when fapi WS URL resolves
- product=spot / VIKE_DISABLE_LIVE / other venues remain byte-identical (no perp worker created)
- Binance perp WS arm is a STANDALONE if+elif pair with Binance spot (NOT chained to OKX/Bybit)
- Spot product with non-empty ws_base_url still uses the spot WS-API worker (no double-wire)
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


def _make_binance_perp_cfg(ws_base_url=""):
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import BinanceHmacSigner
    from vike_trader_app.exec.venue_config import VenueConfig

    creds = Credentials(api_key="K", api_secret="S")  # NO passphrase — Binance doesn't use one
    return VenueConfig(
        venue="binance",
        environment=Environment.DEMO,
        rest_base_url="https://demo-api.binance.com",
        ws_base_url=ws_base_url,
        credentials=creds,
        signer=BinanceHmacSigner(creds, now_ms=lambda: 0),
    )


def _fake_fapi_exchange_info(base_url, path, params=None, **k):
    """Canned Binance /fapi/v1/exchangeInfo response for BTCUSDT."""
    symbol = (params or {}).get("symbol", "BTCUSDT")
    return {
        "symbols": [{
            "symbol": symbol,
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "0.0"},
                {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001",
                 "maxQty": "120.0"},
                {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
            ],
        }],
    }


def _patch_binance_perp_mocks(monkeypatch, cfg, call_log):
    """Common monkeypatching for Binance perp wiring tests."""
    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.binance.transport as btransport
    monkeypatch.setattr(btransport, "get_public_json", _fake_fapi_exchange_info)

    from vike_trader_app.exec.binance import perp_client as pc_mod

    def _spy_set_leverage(self):
        call_log.append("set_leverage")

    monkeypatch.setattr(pc_mod.BinancePerpExecutionClient, "set_leverage", _spy_set_leverage)
    monkeypatch.setattr(
        pc_mod.BinancePerpExecutionClient, "connect",
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


def test_perp_branch_builds_binance_perp_client(app, monkeypatch):
    """VIKE_EXEC_PRODUCT=perp + binance builds BinancePerpExecutionClient, not BinanceSpotExecutionClient."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_binance_perp_cfg()
    call_log = []
    _patch_binance_perp_mocks(monkeypatch, cfg, call_log)

    from vike_trader_app.exec.binance import perp_client as pc_mod
    captured = {}
    real_init = pc_mod.BinancePerpExecutionClient.__init__

    def _spy_init(self, *a, **k):
        captured["type"] = type(self).__name__
        real_init(self, *a, **k)

    monkeypatch.setattr(pc_mod.BinancePerpExecutionClient, "__init__", _spy_init)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True, "_maybe_start_live_exec must return True for binance+perp"
        assert captured.get("type") == "BinancePerpExecutionClient", (
            f"Expected BinancePerpExecutionClient, got {captured.get('type')!r}"
        )
        assert win._exec_session is not None
    finally:
        win.shutdown()


def test_set_leverage_called_before_connect(app, monkeypatch):
    """set_leverage() must be called once (main thread) BEFORE connect/reconcile."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_binance_perp_cfg()
    call_order = []

    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.binance.transport as btransport
    monkeypatch.setattr(btransport, "get_public_json", _fake_fapi_exchange_info)

    from vike_trader_app.exec.binance import perp_client as pc_mod
    monkeypatch.setattr(pc_mod.BinancePerpExecutionClient, "set_leverage",
                        lambda self: call_order.append("set_leverage"))
    monkeypatch.setattr(
        pc_mod.BinancePerpExecutionClient, "connect",
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


def test_client_symbol_is_plain_btcusdt(app, monkeypatch):
    """client_symbol must be the plain 'BTCUSDT' (no dash, no -PERP suffix)."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_binance_perp_cfg()
    call_log = []
    _patch_binance_perp_mocks(monkeypatch, cfg, call_log)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        # hub.symbol must be the plain compact symbol
        hub = win._exec_session._hub
        assert hub.symbol == "BTCUSDT", (
            f"hub.symbol must be 'BTCUSDT' (plain), got {hub.symbol!r}"
        )
    finally:
        win.shutdown()


def test_perp_ws_worker_registered_when_fapi_ws_resolves(app, monkeypatch):
    """binance+perp must register a 'binance' WS worker via make_binance_perp_run_core."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)
    # Ensure the fapi WS resolves to its default (no env override that would suppress it)
    monkeypatch.delenv("BINANCE_DEMO_FAPI_WS_URL", raising=False)

    cfg = _make_binance_perp_cfg()
    call_log = []
    _patch_binance_perp_mocks(monkeypatch, cfg, call_log)

    import vike_trader_app.exec.binance.perp_user_data as pud_mod
    invoked = {}

    def _fake_make_perp_run_core(**kwargs):
        invoked["called"] = True
        invoked["kwargs"] = kwargs
        return lambda emit, stop: None   # no-op run_core

    monkeypatch.setattr(pud_mod, "make_binance_perp_run_core", _fake_make_perp_run_core)

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert invoked.get("called") is True, "make_binance_perp_run_core must be called"
        assert "binance" in win._exec_session._workers, (
            "expected a 'binance' worker in _exec_session._workers"
        )
        # symbol must be the plain BTCUSDT (no dash)
        assert invoked["kwargs"].get("symbol") == "BTCUSDT", (
            f"symbol must be plain BTCUSDT, got {invoked['kwargs'].get('symbol')!r}"
        )
        # api_secret must NOT be passed (listenKey is apiKey-header-only)
        assert "api_secret" not in invoked["kwargs"], (
            "make_binance_perp_run_core must NOT receive api_secret (listenKey is apiKey-only)"
        )
    finally:
        win.shutdown()


def test_perp_ws_worker_not_double_wired_with_spot(app, monkeypatch):
    """binance+perp must NOT also trigger the spot WS-API worker (no double-register)."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "perp")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    # cfg has a non-empty ws_base_url to trigger spot guard if not properly gated
    cfg = _make_binance_perp_cfg(ws_base_url="wss://demo-ws-api.binance.com/ws-api/v3")
    call_log = []
    _patch_binance_perp_mocks(monkeypatch, cfg, call_log)

    import vike_trader_app.exec.binance.perp_user_data as pud_mod
    import vike_trader_app.exec.binance.user_data as spot_ud_mod

    perp_called = []
    spot_called = []

    monkeypatch.setattr(pud_mod, "make_binance_perp_run_core",
                        lambda **kw: (perp_called.append(kw) or (lambda e, s: None)))
    monkeypatch.setattr(spot_ud_mod, "make_binance_run_core",
                        lambda **kw: (spot_called.append(kw) or (lambda e, s: None)))

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert len(perp_called) == 1, (
            f"make_binance_perp_run_core must be called exactly once, got {len(perp_called)}"
        )
        assert len(spot_called) == 0, (
            f"make_binance_run_core (spot) must NOT be called for perp product, "
            f"got {len(spot_called)} calls: {spot_called}"
        )
    finally:
        win.shutdown()


def test_spot_product_uses_spot_ws_worker(app, monkeypatch):
    """VIKE_EXEC_PRODUCT=spot (or unset) + binance must still use the spot WS-API worker."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "spot")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    cfg = _make_binance_perp_cfg(ws_base_url="wss://demo-ws-api.binance.com/ws-api/v3")

    from vike_trader_app.exec import venue_config as vc
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

    import vike_trader_app.exec.binance.transport as btransport

    def _fake_spot_exchange_info(base_url, path, params=None, **k):
        symbol = (params or {}).get("symbol", "BTCUSDT")
        return {
            "symbols": [{
                "symbol": symbol,
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001",
                     "minQty": "0.00001", "maxQty": "9000.0"},
                    {"filterType": "NOTIONAL", "minNotional": "5.0"},
                ],
            }],
        }

    monkeypatch.setattr(btransport, "get_public_json", _fake_spot_exchange_info)

    from vike_trader_app.exec.binance import client as binance_client_mod
    monkeypatch.setattr(
        binance_client_mod.BinanceSpotExecutionClient, "connect",
        lambda self: __import__(
            "vike_trader_app.exec.crypto_client",
            fromlist=["ReconcileSnapshot"],
        ).ReconcileSnapshot(
            positions=((self._symbol, 0.0),),
            position_avg_px=((self._symbol, 0.0),),
        ),
    )

    import vike_trader_app.exec.binance.perp_user_data as pud_mod
    import vike_trader_app.exec.binance.user_data as spot_ud_mod

    perp_called = []
    spot_called = []
    monkeypatch.setattr(pud_mod, "make_binance_perp_run_core",
                        lambda **kw: (perp_called.append(kw) or (lambda e, s: None)))
    monkeypatch.setattr(spot_ud_mod, "make_binance_run_core",
                        lambda **kw: (spot_called.append(kw) or (lambda e, s: None)))

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert len(perp_called) == 0, (
            f"make_binance_perp_run_core must NOT be called for spot product"
        )
        assert len(spot_called) == 1, (
            f"make_binance_run_core (spot) must be called exactly once, got {len(spot_called)}"
        )
    finally:
        win.shutdown()


def test_disable_live_overrides_perp(app, monkeypatch):
    """VIKE_DISABLE_LIVE=1 with binance+perp must return False (paper-safe gate)."""
    monkeypatch.setenv("VIKE_EXEC_VENUE", "binance")
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


def test_other_venue_no_binance_perp_worker(app, monkeypatch):
    """Regression: with VIKE_EXEC_VENUE=okx, the Binance perp arm must be a no-op."""
    from vike_trader_app.exec.credentials import Credentials, Environment
    from vike_trader_app.exec.signer import OKXV5Signer
    from vike_trader_app.exec.venue_config import VenueConfig
    from vike_trader_app.exec import venue_config as vc
    import vike_trader_app.exec.okx.transport as okxtransport
    import vike_trader_app.exec.binance.perp_user_data as pud_mod

    monkeypatch.setenv("VIKE_EXEC_VENUE", "okx")
    monkeypatch.setenv("VIKE_EXEC_ENV", "DEMO")
    monkeypatch.setenv("VIKE_EXEC_PRODUCT", "spot")
    monkeypatch.delenv("VIKE_DISABLE_LIVE", raising=False)

    creds = Credentials(api_key="K", api_secret="S", passphrase="P")
    cfg = VenueConfig(
        venue="okx",
        environment=Environment.DEMO,
        rest_base_url="https://www.okx.com",
        ws_base_url="",
        credentials=creds,
        signer=OKXV5Signer(creds, now_ms=lambda: 0),
    )
    monkeypatch.setattr(vc, "resolve_venue_config", lambda *a, **k: cfg)

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

    perp_called = []
    monkeypatch.setattr(pud_mod, "make_binance_perp_run_core",
                        lambda **kw: (perp_called.append(kw) or (lambda e, s: None)))

    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        ok = win._maybe_start_live_exec()
        assert ok is True
        assert perp_called == [], (
            "make_binance_perp_run_core must NOT be called when venue='okx'"
        )
    finally:
        win.shutdown()
