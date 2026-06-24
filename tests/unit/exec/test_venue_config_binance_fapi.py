"""Unit tests for binance_fapi_rest / binance_fapi_ws helpers in venue_config.

Verifies:
- Default demo fapi REST host
- Default mainnet fapi REST host
- Default demo fapi WS host
- Env-override for both REST and WS knobs
"""
from __future__ import annotations

from vike_trader_app.exec.credentials import Environment
from vike_trader_app.exec import venue_config as vc


def test_demo_fapi_rest_default():
    assert vc.binance_fapi_rest(Environment.DEMO) == "https://demo-fapi.binance.com"


def test_mainnet_fapi_rest_default():
    assert vc.binance_fapi_rest(Environment.MAINNET) == "https://fapi.binance.com"


def test_demo_fapi_ws_default():
    assert vc.binance_fapi_ws(Environment.DEMO) == "wss://fstream.binancefuture.com/ws"


def test_mainnet_fapi_ws_default():
    assert vc.binance_fapi_ws(Environment.MAINNET) == "wss://fstream.binance.com/ws"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("BINANCE_DEMO_FAPI_REST", "https://x")
    monkeypatch.setenv("BINANCE_DEMO_FAPI_WS_URL", "wss://y/ws")
    assert vc.binance_fapi_rest(Environment.DEMO) == "https://x"
    assert vc.binance_fapi_ws(Environment.DEMO) == "wss://y/ws"


def test_mainnet_env_overrides(monkeypatch):
    monkeypatch.setenv("BINANCE_MAINNET_FAPI_REST", "https://mrest")
    monkeypatch.setenv("BINANCE_MAINNET_FAPI_WS_URL", "wss://mws/ws")
    assert vc.binance_fapi_rest(Environment.MAINNET) == "https://mrest"
    assert vc.binance_fapi_ws(Environment.MAINNET) == "wss://mws/ws"


def test_spot_resolve_binance_unchanged():
    """_resolve_binance (spot) is unaffected — still returns demo-api.binance.com REST host."""
    from vike_trader_app.exec.credentials import Credentials
    from vike_trader_app.exec.venue_config import _resolve_binance
    creds = Credentials(api_key="K", api_secret="S")
    cfg = _resolve_binance("binance", Environment.DEMO, creds, now_ms=lambda: 0)
    assert "demo-api.binance.com" in cfg.rest_base_url
    assert "fapi" not in cfg.rest_base_url, "spot resolve must not return fapi host"
