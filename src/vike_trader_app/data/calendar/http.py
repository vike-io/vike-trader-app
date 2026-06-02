"""Tiny urllib JSON/text getters (matches data/binance_source.py's stdlib approach).

Injected into providers as the default `http` so tests can pass a fake and never
touch the network.
"""
from __future__ import annotations

import json
import urllib.request


def http_get_text(url: str, *, timeout: int = 30, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "vike-trader-app"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
        return resp.read().decode("utf-8")


def http_get_json(url: str, *, timeout: int = 30, headers: dict | None = None):
    return json.loads(http_get_text(url, timeout=timeout, headers=headers))
