"""Deribit public/auth frame builders: exact JSON-RPC shape; secrets never appear except in the frame."""
from vike_trader_app.exec.deribit.ws_auth import (
    build_client_credentials_auth,
    build_private_subscribe,
    build_refresh_token_auth,
)


def test_client_credentials_frame_shape():
    frame = build_client_credentials_auth(
        client_id="CID", client_secret="CSECRET", scope="connection", rpc_id=9929)
    assert frame == {
        "jsonrpc": "2.0",
        "id": 9929,
        "method": "public/auth",
        "params": {
            "grant_type": "client_credentials",
            "client_id": "CID",
            "client_secret": "CSECRET",
            "scope": "connection",
        },
    }


def test_client_credentials_omits_scope_when_none():
    frame = build_client_credentials_auth(client_id="CID", client_secret="CSECRET", rpc_id=1)
    assert "scope" not in frame["params"]
    assert frame["params"]["grant_type"] == "client_credentials"


def test_refresh_token_frame_shape():
    frame = build_refresh_token_auth(refresh_token="RT123", rpc_id=42)
    assert frame == {
        "jsonrpc": "2.0",
        "id": 42,
        "method": "public/auth",
        "params": {"grant_type": "refresh_token", "refresh_token": "RT123"},
    }


def test_build_private_subscribe_shape():
    frame = build_private_subscribe(channels=["user.trades.option.BTC.raw"], rpc_id=7)
    assert frame == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "private/subscribe",
        "params": {"channels": ["user.trades.option.BTC.raw"]},
    }


def test_build_private_subscribe_multiple_channels():
    frame = build_private_subscribe(
        channels=["user.trades.option.BTC.raw", "user.trades.option.ETH.raw"], rpc_id=3)
    assert frame["params"]["channels"] == [
        "user.trades.option.BTC.raw", "user.trades.option.ETH.raw"]
    assert frame["id"] == 3
