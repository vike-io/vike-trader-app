"""Credentials: env-var-name map + presence gate; no keyring import."""

import importlib

import pytest

from vike_trader_app.exec.credentials import (
    Credentials,
    Environment,
    env_var_names,
    load_credentials,
)


def test_env_var_names_is_the_single_naming_site():
    assert env_var_names("binance", Environment.DEMO) == (
        "BINANCE_DEMO_API_KEY", "BINANCE_DEMO_API_SECRET", "BINANCE_DEMO_PASSPHRASE")
    assert env_var_names("binance", Environment.MAINNET) == (
        "BINANCE_MAINNET_API_KEY", "BINANCE_MAINNET_API_SECRET", "BINANCE_MAINNET_PASSPHRASE")


def test_load_returns_credentials_when_set(monkeypatch):
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "k123")
    monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "s456")
    creds = load_credentials("binance", Environment.DEMO)
    assert isinstance(creds, Credentials)
    assert (creds.api_key, creds.api_secret, creds.passphrase) == ("k123", "s456", None)


def test_load_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("BINANCE_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_DEMO_API_SECRET", raising=False)
    assert load_credentials("binance", Environment.DEMO) is None


def test_load_returns_none_when_blank(monkeypatch):
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "")
    monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "s456")
    assert load_credentials("binance", Environment.DEMO) is None


def test_no_keyring_dependency():
    mod = importlib.import_module("vike_trader_app.exec.credentials")
    assert "keyring" not in getattr(mod, "__dict__", {})


def test_repr_masks_the_secret():
    creds = Credentials(api_key="abcdef1234", api_secret="TOPSECRET", passphrase="pp")
    r = repr(creds)
    assert "TOPSECRET" not in r and "pp" not in r
    assert "***1234" in r
