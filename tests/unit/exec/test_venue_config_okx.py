"""OKX venue dispatch: demo AND mainnet share https://www.okx.com (x-simulated-trading header
distinguishes — a transport concern, not a URL concern); empty WS (deferred); OKXV5Signer;
absent creds -> None; passphrase required."""

from vike_trader_app.exec.credentials import Credentials, Environment
from vike_trader_app.exec.signer import OKXV5Signer
from vike_trader_app.exec.venue_config import (
    OKX_REST,
    resolve_venue_config,
)


def _load_ok(venue, env):
    return Credentials(api_key="K", api_secret="S", passphrase="P")


def _load_none(venue, env):
    return None


def test_okx_absent_creds_returns_none():
    assert resolve_venue_config("okx", Environment.DEMO,
                                now_ms=lambda: 0, load=_load_none) is None


def test_okx_demo_uses_okx_host_empty_ws_and_okx_signer():
    cfg = resolve_venue_config("okx", Environment.DEMO, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == OKX_REST
    assert cfg.ws_base_url == ""  # WS deferred
    assert isinstance(cfg.signer, OKXV5Signer)


def test_okx_mainnet_uses_same_host():
    """OKX demo and mainnet share the same REST host — the x-simulated-trading header distinguishes."""
    cfg = resolve_venue_config("okx", Environment.MAINNET, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == OKX_REST


def test_okx_demo_env_override(monkeypatch):
    monkeypatch.setenv("OKX_DEMO_BASE_URL", "https://okx.example.test")
    cfg = resolve_venue_config("okx", Environment.DEMO, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == "https://okx.example.test"


def test_okx_mainnet_env_override(monkeypatch):
    monkeypatch.setenv("OKX_MAINNET_BASE_URL", "https://okx.main.test")
    cfg = resolve_venue_config("okx", Environment.MAINNET, now_ms=lambda: 0, load=_load_ok)
    assert cfg.rest_base_url == "https://okx.main.test"
