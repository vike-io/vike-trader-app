"""Bybit venue dispatch: demo/mainnet REST + WS URLs, BybitV5Signer; absent creds -> None.
Binance dispatch is unchanged (covered by test_venue_config.py)."""

from vike_trader_app.exec.credentials import Credentials, Environment
from vike_trader_app.exec.signer import BybitV5Signer
from vike_trader_app.exec.venue_config import (
    BYBIT_DEMO_REST,
    BYBIT_DEMO_WS_DEFAULT,
    BYBIT_MAINNET_REST,
    BYBIT_MAINNET_WS_DEFAULT,
    resolve_venue_config,
)


def _load_ok(venue, env):
    return Credentials(api_key="K", api_secret="S")


def _load_none(venue, env):
    return None


def test_bybit_absent_creds_returns_none():
    assert resolve_venue_config("bybit", Environment.DEMO,
                                now_ms=lambda: 0, load=_load_none) is None


def test_bybit_demo_uses_demo_rest_demo_ws_and_bybit_signer():
    cfg = resolve_venue_config("bybit", Environment.DEMO, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == BYBIT_DEMO_REST
    assert cfg.ws_base_url == BYBIT_DEMO_WS_DEFAULT
    assert cfg.ws_base_url == "wss://stream-demo.bybit.com/v5/private"
    assert isinstance(cfg.signer, BybitV5Signer)


def test_bybit_mainnet_uses_production_rest_and_mainnet_ws():
    cfg = resolve_venue_config("bybit", Environment.MAINNET, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == BYBIT_MAINNET_REST
    assert cfg.ws_base_url == BYBIT_MAINNET_WS_DEFAULT
    assert cfg.ws_base_url == "wss://stream.bybit.com/v5/private"


def test_bybit_env_override_of_base_url(monkeypatch):
    monkeypatch.setenv("BYBIT_DEMO_BASE_URL", "https://bybit.example.test")
    cfg = resolve_venue_config("bybit", Environment.DEMO, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == "https://bybit.example.test"


def test_bybit_env_override_of_ws_url(monkeypatch):
    monkeypatch.setenv("BYBIT_DEMO_WS_URL", "wss://bybit-ws.example.test/v5/private")
    cfg = resolve_venue_config("bybit", Environment.DEMO, now_ms=lambda: 0, load=_load_ok)
    assert cfg.ws_base_url == "wss://bybit-ws.example.test/v5/private"
