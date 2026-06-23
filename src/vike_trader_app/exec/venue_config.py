"""Testnet ladder: ONE adapter, swap base-URL + creds + signer by (venue, Environment).

REST and WS base URLs are SEPARATE, env-overridable knobs — the demo REST host is verified
(demo-api.binance.com) but the matching demo user-data WS host must be confirmed empirically
(Task 15) and is overridable via BINANCE_DEMO_WS_URL. resolve_venue_config returns None when
creds are absent (the live gate).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from vike_trader_app.exec.credentials import Credentials, Environment, load_credentials
from vike_trader_app.exec.signer import BinanceHmacSigner

BINANCE_DEMO_REST = "https://demo-api.binance.com"
BINANCE_MAINNET_REST = "https://api.binance.com"
# PLACEHOLDER — confirm the demo user-data WS host empirically (Task 15). Override via env meanwhile.
BINANCE_DEMO_WS_DEFAULT = "wss://stream.binance.com:9443/ws"
BINANCE_MAINNET_WS_DEFAULT = "wss://stream.binance.com:9443/ws"


@dataclass(frozen=True)
class VenueConfig:
    venue: str
    environment: Environment
    rest_base_url: str
    ws_base_url: str
    credentials: Credentials
    signer: object


def resolve_venue_config(venue: str, env: Environment, *, now_ms: Callable[[], int],
                         load: Callable[[str, Environment], Credentials | None] = load_credentials
                         ) -> VenueConfig | None:
    """Build a VenueConfig, or None when creds are absent (stay paper)."""
    creds = load(venue, env)
    if creds is None:
        return None
    if env is Environment.MAINNET:
        rest = os.environ.get("BINANCE_MAINNET_BASE_URL") or BINANCE_MAINNET_REST
        ws = os.environ.get("BINANCE_MAINNET_WS_URL") or BINANCE_MAINNET_WS_DEFAULT
    else:
        rest = os.environ.get("BINANCE_DEMO_BASE_URL") or BINANCE_DEMO_REST
        ws = os.environ.get("BINANCE_DEMO_WS_URL") or BINANCE_DEMO_WS_DEFAULT
    signer = BinanceHmacSigner(creds, now_ms=now_ms)
    return VenueConfig(venue=venue, environment=env, rest_base_url=rest, ws_base_url=ws,
                       credentials=creds, signer=signer)
