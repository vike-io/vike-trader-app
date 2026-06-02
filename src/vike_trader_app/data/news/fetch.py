"""Network layer: stdlib urllib GET + concurrent multi-feed fetch. Kept thin and DI-friendly.

Per-feed failures are swallowed (logged) so one dead/moved feed never breaks the rest — the
same defensive posture as the background symbol-load. Not unit-tested against the network;
``fetch_all`` accepts an injectable ``fetcher`` for deterministic tests.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def _resolve_jobs(specs, symbol):
    """(spec, url) for every enabled provider whose URL resolves for ``symbol``."""
    jobs = []
    for spec in specs:
        if not spec.enabled:
            continue
        url = build_url(spec, symbol)
        if url:
            jobs.append((spec, url))
    return jobs


def _fetch_parse(spec, url, fetcher) -> list[NewsItem]:
    """One feed: fetch + parse, isolated — any failure yields [] (logged), never raises."""
    try:
        data = fetcher(url)
        return parse_feed(data, source=spec.name, market=spec.market) if data else []
    except Exception as exc:  # noqa: BLE001 - never let one feed kill the batch
        log.warning("news parse failed %s: %s", spec.name, exc)
        return []


def fetch_iter(specs, symbol, *, fetcher=fetch_feed, max_workers: int = 8) -> Iterator[list[NewsItem]]:
    """Yield each feed's parsed items **as soon as that feed completes** (incremental render).

    Feeds still run concurrently; results arrive in completion order so the UI can paint the
    first feed without waiting for the slowest. Empty/dead feeds yield nothing.
    """
    jobs = _resolve_jobs(specs, symbol)
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_fetch_parse, spec, url, fetcher) for spec, url in jobs]
        for fut in as_completed(futures):
            items = fut.result()
            if items:
                yield items


def fetch_all(specs, symbol, *, fetcher=fetch_feed, max_workers: int = 8) -> list[NewsItem]:
    """Eager variant: every feed's items flattened into one list (one-shot callers/tests)."""
    items: list[NewsItem] = []
    for chunk in fetch_iter(specs, symbol, fetcher=fetcher, max_workers=max_workers):
        items.extend(chunk)
    return items
