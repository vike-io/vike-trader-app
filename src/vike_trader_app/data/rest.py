"""Tiny JSON-over-HTTP GET with a browser-ish User-Agent.

Several exchange REST APIs (OKX, Coinbase Exchange) return ``403 Forbidden`` to the default
``Python-urllib`` agent, so every crypto-breadth source funnels through here.
"""

import json
import urllib.request

_UA = "Mozilla/5.0 (compatible; vike-trader-app/1.0; +https://vike.io)"


def get_json(url: str, timeout: int = 30):
    """GET ``url`` with a User-Agent header and parse the JSON body."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
        return json.loads(resp.read())
