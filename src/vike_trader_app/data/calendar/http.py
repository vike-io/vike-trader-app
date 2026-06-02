"""Tiny urllib JSON/text getters (matches data/binance_source.py's stdlib approach).

Injected into providers as the default `http` so tests can pass a fake and never
touch the network.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


def http_get_text(url: str, *, timeout: int = 30, headers: dict | None = None,
                  retries: int = 2, backoff: float = 0.7) -> str:
    """GET text, retrying on throttling. FRED/BLS/etc. return HTTP 429 when many
    series are fetched back-to-back; a short backoff (honoring Retry-After) recovers
    without dropping the actual. Runs on the fetch worker thread, so sleeping is fine."""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "vike-trader-app"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < retries:
                retry_after = (exc.headers or {}).get("Retry-After") if hasattr(exc, "headers") else None
                wait = float(retry_after) if retry_after and str(retry_after).isdigit() \
                    else backoff * (attempt + 1)
                time.sleep(min(wait, 5.0))
                continue
            raise


def http_get_json(url: str, *, timeout: int = 30, headers: dict | None = None):
    return json.loads(http_get_text(url, timeout=timeout, headers=headers))
