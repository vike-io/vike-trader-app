"""VenueConfig resolution: creds-absent -> None (gate); env override of base URLs; signer built."""

import pytest

from vike_trader_app.exec.credentials import Credentials, Environment
from vike_trader_app.exec.venue_config import (
    BINANCE_DEMO_REST,
    resolve_venue_config,
)


def _load_ok(venue, env):
    return Credentials(api_key="K", api_secret="S")


def _load_none(venue, env):
    return None


def test_absent_creds_returns_none():
    assert resolve_venue_config("binance", Environment.DEMO,
                                now_ms=lambda: 0, load=_load_none) is None


def test_demo_defaults_to_demo_host():
    cfg = resolve_venue_config("binance", Environment.DEMO, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == BINANCE_DEMO_REST
    assert cfg.ws_base_url  # non-empty default
    assert cfg.signer is not None


def test_env_override_of_base_urls(monkeypatch):
    monkeypatch.setenv("BINANCE_DEMO_BASE_URL", "https://example.test")
    monkeypatch.setenv("BINANCE_DEMO_WS_URL", "wss://ws.example.test/ws")
    cfg = resolve_venue_config("binance", Environment.DEMO, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == "https://example.test"
    assert cfg.ws_base_url == "wss://ws.example.test/ws"


def test_mainnet_uses_production_rest():
    cfg = resolve_venue_config("binance", Environment.MAINNET, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == "https://api.binance.com"
