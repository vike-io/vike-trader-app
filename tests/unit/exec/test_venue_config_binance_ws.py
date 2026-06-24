"""Binance private-WS host resolution tests: demo ws-api + mainnet, env-overridable."""

from vike_trader_app.exec.credentials import Credentials, Environment
from vike_trader_app.exec.venue_config import (
    BINANCE_DEMO_WS_DEFAULT, BINANCE_MAINNET_WS_DEFAULT, resolve_venue_config)

_CREDS = Credentials(api_key="K", api_secret="S")


def _load(_v, _e):
    return _CREDS


def test_binance_demo_ws_is_wsapi_host():
    cfg = resolve_venue_config("binance", Environment.DEMO, now_ms=lambda: 0, load=_load)
    assert cfg.ws_base_url == "wss://demo-ws-api.binance.com/ws-api/v3"
    assert cfg.ws_base_url == BINANCE_DEMO_WS_DEFAULT


def test_binance_mainnet_ws_is_wsapi_host():
    cfg = resolve_venue_config("binance", Environment.MAINNET, now_ms=lambda: 0, load=_load)
    assert cfg.ws_base_url == "wss://ws-api.binance.com/ws-api/v3"
    assert cfg.ws_base_url == BINANCE_MAINNET_WS_DEFAULT


def test_binance_demo_ws_env_override(monkeypatch):
    monkeypatch.setenv("BINANCE_DEMO_WS_URL", "wss://override/ws-api/v3")
    cfg = resolve_venue_config("binance", Environment.DEMO, now_ms=lambda: 0, load=_load)
    assert cfg.ws_base_url == "wss://override/ws-api/v3"


def test_other_venue_ws_unchanged(monkeypatch):
    # regression guard: bybit/okx WS hosts must be untouched by this edit
    for ev in ("BYBIT_DEMO_WS_URL", "OKX_DEMO_WS_URL"):
        monkeypatch.delenv(ev, raising=False)
    b = resolve_venue_config("bybit", Environment.DEMO, now_ms=lambda: 0,
                             load=lambda v, e: Credentials("K", "S"))
    assert b.ws_base_url == "wss://stream-demo.bybit.com/v5/private"
    o = resolve_venue_config("okx", Environment.DEMO, now_ms=lambda: 0,
                             load=lambda v, e: Credentials("K", "S", "P"))
    assert o.ws_base_url == "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"
