"""Testnet ladder: ONE adapter, swap base-URL + creds + signer by (venue, Environment).

REST and WS base URLs are SEPARATE, env-overridable knobs — the demo REST host is verified
(demo-api.binance.com) and the matching demo WebSocket-API host (demo-ws-api.binance.com) is
overridable via BINANCE_DEMO_WS_URL. resolve_venue_config returns None when creds are absent
(the live gate).

Binance demo and mainnet both use the WebSocket-API session-based signed-subscribe model for
fill/user-data — NOT the legacy listenKey stream (which is 410 Gone on demo). The demo WS host
(demo-ws-api.binance.com) aligns with the demo REST credentials; mainnet is the production
WS-API host (ws-api.binance.com). Both are env-overridable for testnet ladder testing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from vike_trader_app.exec.credentials import Credentials, Environment, load_credentials
from vike_trader_app.exec.signer import BinanceHmacSigner, BybitV5Signer, OKXV5Signer

BINANCE_DEMO_REST = "https://demo-api.binance.com"
BINANCE_MAINNET_REST = "https://api.binance.com"
BINANCE_DEMO_WS_DEFAULT = "wss://demo-ws-api.binance.com/ws-api/v3"
BINANCE_MAINNET_WS_DEFAULT = "wss://ws-api.binance.com/ws-api/v3"

BYBIT_DEMO_REST = "https://api-demo.bybit.com"
BYBIT_MAINNET_REST = "https://api.bybit.com"
BYBIT_DEMO_WS_DEFAULT = "wss://stream-demo.bybit.com/v5/private"
BYBIT_MAINNET_WS_DEFAULT = "wss://stream.bybit.com/v5/private"

# OKX: demo and mainnet share the same REST host; the x-simulated-trading:1 header (a transport
# concern) is the only distinction between demo and mainnet at the HTTP level.
OKX_REST = "https://www.okx.com"
OKX_DEMO_WS_DEFAULT = "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"
OKX_MAINNET_WS_DEFAULT = "wss://ws.okx.com:8443/ws/v5/private"

# Binance fapi (USDS-M perpetuals) — separate REST + WS hosts from the spot API.
# Demo fapi REST is verified live (demo-fapi.binance.com). The demo fapi WS default
# (fstream.binancefuture.com) is the testnet-adjacent host; overridable via env if
# a different host is confirmed empirically via @pytest.mark.network smoke.
BINANCE_DEMO_FAPI_REST = "https://demo-fapi.binance.com"
BINANCE_MAINNET_FAPI_REST = "https://fapi.binance.com"
BINANCE_DEMO_FAPI_WS_DEFAULT = "wss://fstream.binancefuture.com/ws"
BINANCE_MAINNET_FAPI_WS_DEFAULT = "wss://fstream.binance.com/ws"


def binance_fapi_rest(env: Environment) -> str:
    """Return the env-overridable fapi REST base URL for the given Environment."""
    if env is Environment.MAINNET:
        return os.environ.get("BINANCE_MAINNET_FAPI_REST") or BINANCE_MAINNET_FAPI_REST
    return os.environ.get("BINANCE_DEMO_FAPI_REST") or BINANCE_DEMO_FAPI_REST


def binance_fapi_ws(env: Environment) -> str:
    """Return the env-overridable fapi WS base URL for the given Environment."""
    if env is Environment.MAINNET:
        return os.environ.get("BINANCE_MAINNET_FAPI_WS_URL") or BINANCE_MAINNET_FAPI_WS_DEFAULT
    return os.environ.get("BINANCE_DEMO_FAPI_WS_URL") or BINANCE_DEMO_FAPI_WS_DEFAULT


@dataclass(frozen=True)
class VenueConfig:
    venue: str
    environment: Environment
    rest_base_url: str
    ws_base_url: str
    credentials: Credentials
    signer: object


def _resolve_binance(venue: str, env: Environment, creds: Credentials,
                     now_ms: Callable[[], int]) -> VenueConfig:
    if env is Environment.MAINNET:
        rest = os.environ.get("BINANCE_MAINNET_BASE_URL") or BINANCE_MAINNET_REST
        ws = os.environ.get("BINANCE_MAINNET_WS_URL") or BINANCE_MAINNET_WS_DEFAULT
    else:
        rest = os.environ.get("BINANCE_DEMO_BASE_URL") or BINANCE_DEMO_REST
        ws = os.environ.get("BINANCE_DEMO_WS_URL") or BINANCE_DEMO_WS_DEFAULT
    signer = BinanceHmacSigner(creds, now_ms=now_ms)
    return VenueConfig(venue=venue, environment=env, rest_base_url=rest, ws_base_url=ws,
                       credentials=creds, signer=signer)


def _resolve_bybit(venue: str, env: Environment, creds: Credentials,
                   now_ms: Callable[[], int]) -> VenueConfig:
    if env is Environment.MAINNET:
        rest = os.environ.get("BYBIT_MAINNET_BASE_URL") or BYBIT_MAINNET_REST
        ws = os.environ.get("BYBIT_MAINNET_WS_URL") or BYBIT_MAINNET_WS_DEFAULT
    else:
        rest = os.environ.get("BYBIT_DEMO_BASE_URL") or BYBIT_DEMO_REST
        ws = os.environ.get("BYBIT_DEMO_WS_URL") or BYBIT_DEMO_WS_DEFAULT
    signer = BybitV5Signer(creds, now_ms=now_ms)
    return VenueConfig(venue=venue, environment=env, rest_base_url=rest, ws_base_url=ws,
                       credentials=creds, signer=signer)


def _resolve_okx(venue: str, env: Environment, creds: Credentials,
                 now_ms: Callable[[], int]) -> VenueConfig:
    # Demo and mainnet share the same REST host; env override still supported for test injection.
    if env is Environment.MAINNET:
        rest = os.environ.get("OKX_MAINNET_BASE_URL") or OKX_REST
        ws = os.environ.get("OKX_MAINNET_WS_URL") or OKX_MAINNET_WS_DEFAULT
    else:
        rest = os.environ.get("OKX_DEMO_BASE_URL") or OKX_REST
        ws = os.environ.get("OKX_DEMO_WS_URL") or OKX_DEMO_WS_DEFAULT
    signer = OKXV5Signer(creds, now_ms=now_ms)
    return VenueConfig(venue=venue, environment=env, rest_base_url=rest, ws_base_url=ws,
                       credentials=creds, signer=signer)


_VENUE_BUILDERS = {"binance": _resolve_binance, "bybit": _resolve_bybit, "okx": _resolve_okx}


def resolve_venue_config(venue: str, env: Environment, *, now_ms: Callable[[], int],
                         load: Callable[[str, Environment], Credentials | None] = load_credentials
                         ) -> VenueConfig | None:
    """Build a VenueConfig, or None when creds are absent (stay paper)."""
    creds = load(venue, env)
    if creds is None:
        return None
    builder = _VENUE_BUILDERS.get(venue.lower())
    if builder is None:
        return None
    return builder(venue, env, creds, now_ms)
