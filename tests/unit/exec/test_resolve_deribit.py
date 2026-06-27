"""Unit tests for _resolve_deribit in venue_config."""
from __future__ import annotations
import pytest
from vike_trader_app.exec.credentials import Credentials, Environment
from vike_trader_app.exec.venue_config import (
    VenueConfig, DERIBIT_MAINNET_REST, DERIBIT_MAINNET_WS,
    DERIBIT_TESTNET_REST, DERIBIT_TESTNET_WS, resolve_venue_config,
)


def _creds():
    return Credentials(api_key="cid", api_secret="csec", passphrase="")


def _load(venue, env):
    return _creds()


def test_deribit_mainnet_hosts():
    cfg = resolve_venue_config("deribit", Environment.MAINNET, now_ms=lambda: 0, load=_load)
    assert cfg is not None
    assert cfg.signer is None
    assert cfg.rest_base_url == DERIBIT_MAINNET_REST
    assert cfg.ws_base_url == DERIBIT_MAINNET_WS


def test_deribit_demo_hosts():
    cfg = resolve_venue_config("deribit", Environment.DEMO, now_ms=lambda: 0, load=_load)
    assert cfg is not None
    assert cfg.signer is None
    assert cfg.rest_base_url == DERIBIT_TESTNET_REST
    assert cfg.ws_base_url == DERIBIT_TESTNET_WS


def test_deribit_env_override(monkeypatch):
    monkeypatch.setenv("DERIBIT_MAINNET_REST_URL", "https://custom.deribit.com")
    cfg = resolve_venue_config("deribit", Environment.MAINNET, now_ms=lambda: 0, load=_load)
    assert cfg.rest_base_url == "https://custom.deribit.com"


def test_existing_venues_signer_unchanged():
    """VenueConfig.signer=None default must not break the 3 existing venues (they pass signer= explicitly)."""
    from vike_trader_app.exec.signer import BinanceHmacSigner
    creds = Credentials(api_key="k", api_secret="s", passphrase="")
    signer = BinanceHmacSigner(creds, now_ms=lambda: 0)
    cfg = VenueConfig(venue="binance", environment=Environment.DEMO,
                      rest_base_url="https://x", ws_base_url="wss://y",
                      credentials=creds, signer=signer)
    assert cfg.signer is signer
