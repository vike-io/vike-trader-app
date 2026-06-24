"""OKX private-WS host resolution tests: demo wspap + mainnet, env-overridable."""

from vike_trader_app.exec.credentials import Credentials, Environment
from vike_trader_app.exec.venue_config import (
    OKX_DEMO_WS_DEFAULT, OKX_MAINNET_WS_DEFAULT, resolve_venue_config)

_CREDS = Credentials(api_key="K", api_secret="S", passphrase="P")


def _load(_v, _e):
    return _CREDS


def test_okx_demo_ws_url():
    cfg = resolve_venue_config("okx", Environment.DEMO, now_ms=lambda: 0, load=_load)
    assert cfg.ws_base_url == "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"
    assert cfg.ws_base_url == OKX_DEMO_WS_DEFAULT


def test_okx_mainnet_ws_url():
    cfg = resolve_venue_config("okx", Environment.MAINNET, now_ms=lambda: 0, load=_load)
    assert cfg.ws_base_url == "wss://ws.okx.com:8443/ws/v5/private"
    assert cfg.ws_base_url == OKX_MAINNET_WS_DEFAULT


def test_okx_demo_ws_env_override(monkeypatch):
    monkeypatch.setenv("OKX_DEMO_WS_URL", "wss://override/private")
    cfg = resolve_venue_config("okx", Environment.DEMO, now_ms=lambda: 0, load=_load)
    assert cfg.ws_base_url == "wss://override/private"


def test_bybit_ws_unchanged():
    cfg = resolve_venue_config("bybit", Environment.DEMO, now_ms=lambda: 0, load=_load)
    assert cfg.ws_base_url == "wss://stream-demo.bybit.com/v5/private"


def test_binance_demo_ws_unchanged():
    cfg = resolve_venue_config("binance", Environment.DEMO, now_ms=lambda: 0, load=_load)
    assert cfg.ws_base_url == ""  # demo has no listenKey stream — still empty (regression guard)
