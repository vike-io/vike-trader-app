"""Public Deribit option-instrument fetcher (no credentials required).

GET /api/v2/public/get_instruments?currency={currency}&kind=option
Returns a {instrument_name: filters} dict via parse_deribit_option_instruments.
Injectable transport for offline tests (same pattern as data/options/deribit.py).
"""
from __future__ import annotations

import json
import urllib.request

from vike_trader_app.exec.deribit.instruments import parse_deribit_option_instruments


def _urllib_get(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — fixed https host
        return json.loads(resp.read())


def fetch_option_instruments(
    currency: str,
    *,
    base_url: str,
    transport=None,
    timeout: float = 10.0,
) -> dict[str, dict]:
    """Return {instrument_name: filters} for all options on ``currency``.

    ``transport(url) -> dict`` is injectable for tests; defaults to urllib GET.
    ``base_url`` is the https REST host (e.g. 'https://www.deribit.com' or 'https://test.deribit.com').
    """
    url = f"{base_url.rstrip('/')}/api/v2/public/get_instruments?currency={currency}&kind=option"
    if transport is None:
        payload = _urllib_get(url, timeout=timeout)
    else:
        payload = transport(url)
    return parse_deribit_option_instruments(payload)
