"""Deribit public/auth JSON-RPC frame builders.

grant_type=client_credentials -> {client_id, client_secret, scope?}; grant_type=refresh_token ->
{refresh_token}. The response (parsed elsewhere) carries access_token + refresh_token + expires_in
(SECONDS). client_signature (HMAC over timestamp+nonce+data) is the no-secret-transmission alternative
and would mirror okx/ws_auth.py:11 — deferred until creds arrive.

NEVER log a frame returned here: it carries client_id + client_secret in plaintext. The structural
guarantee (the worker closure is the only holder; events/errors carry no creds) is the primary defense;
this docstring is the operational reminder.
"""
from __future__ import annotations


def build_client_credentials_auth(*, client_id: str, client_secret: str,
                                  scope: str | None = None, rpc_id: int) -> dict:
    """Build the public/auth client_credentials frame. NEVER log the return value."""
    params: dict = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope is not None:
        params["scope"] = scope
    return {"jsonrpc": "2.0", "id": rpc_id, "method": "public/auth", "params": params}


def build_refresh_token_auth(*, refresh_token: str, rpc_id: int) -> dict:
    """Build the public/auth refresh_token frame (long-lived-socket token refresh)."""
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "public/auth",
        "params": {"grant_type": "refresh_token", "refresh_token": refresh_token},
    }


def build_private_subscribe(*, channels: list[str], rpc_id: int) -> dict:
    """Build the private/subscribe frame for the authed socket's user channels.

    On Deribit the subscription is bound to the AUTHENTICATED socket; a later refresh_token renews
    the session in place WITHOUT re-subscribing. Safe to log (carries no creds — only channel names).
    """
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "private/subscribe",
        "params": {"channels": list(channels)},
    }
