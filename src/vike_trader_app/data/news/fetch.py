"""Network layer: stdlib urllib GET + concurrent multi-feed fetch. Kept thin and DI-friendly.

Per-feed failures are swallowed (logged) so one dead/moved feed never breaks the rest — the
same defensive posture as the background symbol-load. Not unit-tested against the network;
``fetch_all`` accepts an injectable ``fetcher`` for deterministic tests.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .models import NewsItem
from .providers import build_url
from .rss import parse_feed

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (vike-trader-app news reader)"
_TIMEOUT = 6.0


def fetch_feed(url: str, *, timeout: float = _TIMEOUT) -> bytes | None:
    """GET ``url`` → bytes, or None on any network/HTTP error (logged, never raised)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("news fetch failed %s: %s", url, exc)
        return None


def fetch_all(specs, symbol, *, fetcher=fetch_feed, max_workers: int = 8) -> list[NewsItem]:
    """Fetch every enabled, resolvable provider concurrently and return parsed items."""
    jobs = []
    for spec in specs:
        if not spec.enabled:
            continue
        url = build_url(spec, symbol)
        if url:
            jobs.append((spec, url))
    if not jobs:
        return []

    def _one(job):
        spec, url = job
        try:
            data = fetcher(url)
            return parse_feed(data, source=spec.name, market=spec.market) if data else []
        except Exception as exc:  # noqa: BLE001 - never let one feed kill the batch
            log.warning("news parse failed %s: %s", spec.name, exc)
            return []

    items: list[NewsItem] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for result in ex.map(_one, jobs):
            items.extend(result)
    return items
