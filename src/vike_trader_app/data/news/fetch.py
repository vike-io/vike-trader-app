"""Network layer: stdlib urllib GET + concurrent multi-feed fetch. Kept thin and DI-friendly.

Per-feed failures are swallowed (logged) so one dead/moved feed never breaks the rest — the
same defensive posture as the background symbol-load. Not unit-tested against the network;
``fetch_all`` accepts an injectable ``fetcher`` for deterministic tests.
"""

from __future__ import annotations

import logging
import threading
import time
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

# Statuses that mean "the server is pushing back" — back off instead of re-hitting every cycle.
# Anything else (404, DNS failure, timeout, …) keeps the legacy try-again-next-refresh behavior.
_RATE_LIMIT_STATUSES = frozenset({429, 403})


def _parse_retry_after(value) -> float | None:
    """``Retry-After`` header → seconds, or None. Only the numeric form is honored; the
    HTTP-date form (e.g. ``Wed, 21 Oct 2026 07:28:00 GMT``) falls back to the base backoff."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class FeedThrottle:
    """Per-URL rate-limit backoff + short success-TTL cache for feed fetches.

    Rate-limited URLs (429/403) are blocked for ``backoff`` seconds — doubled on each
    consecutive failure, capped at ``max_backoff`` — or for the server's numeric
    ``Retry-After`` when supplied. Successful bytes are served from cache for
    ``success_ttl`` seconds so back-to-back refreshes don't re-hit healthy feeds.

    Thread-safe: ``fetch_iter`` runs fetches from a ThreadPoolExecutor. ``now`` is
    injectable (monotonic clock) for deterministic tests.
    """

    def __init__(self, *, now=time.monotonic, backoff: float = 120.0,
                 success_ttl: float = 30.0, max_backoff: float = 1800.0) -> None:
        self._now = now
        self._backoff = backoff
        self._success_ttl = success_ttl
        self._max_backoff = max_backoff
        self._lock = threading.Lock()
        self._blocked_until: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._cache: dict[str, tuple[bytes, float]] = {}

    def allow(self, url: str) -> bool:
        """False while ``url`` is inside its backoff window."""
        with self._lock:
            blocked_until = self._blocked_until.get(url)
            return blocked_until is None or self._now() >= blocked_until

    def cached(self, url: str) -> bytes | None:
        """Last successful bytes for ``url`` if still within ``success_ttl`` (0 disables)."""
        with self._lock:
            entry = self._cache.get(url)
            if entry is None or self._success_ttl <= 0:
                return None
            data, cached_at = entry
            return data if self._now() - cached_at < self._success_ttl else None

    def record_failure(self, url: str, *, status: int | None = None,
                       retry_after_s: float | None = None) -> None:
        """Open/extend the block window for rate-limit statuses; anything else is a no-op."""
        if status not in _RATE_LIMIT_STATUSES:
            return
        with self._lock:
            failures = self._failures.get(url, 0) + 1
            self._failures[url] = failures
            window = retry_after_s if retry_after_s is not None \
                else self._backoff * 2 ** (failures - 1)
            self._blocked_until[url] = self._now() + min(window, self._max_backoff)

    def record_success(self, url: str, data: bytes) -> None:
        """Clear any failure state and cache ``data`` for ``success_ttl`` seconds."""
        with self._lock:
            self._failures.pop(url, None)
            self._blocked_until.pop(url, None)
            self._cache[url] = (data, self._now())


_THROTTLE = FeedThrottle()


def fetch_feed(url: str, *, timeout: float = _TIMEOUT,
               throttle: FeedThrottle | None = _THROTTLE) -> bytes | None:
    """GET ``url`` → bytes, or None on any network/HTTP error (logged, never raised).

    ``throttle`` adds per-URL 429/403 backoff + a short success-TTL cache (module-wide
    singleton by default); pass None for the legacy always-fetch behavior.
    """
    if throttle is not None:
        data = throttle.cached(url)
        if data is not None:
            return data
        if not throttle.allow(url):
            return None
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        log.warning("news fetch failed %s: %s", url, exc)
        if throttle is not None:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            throttle.record_failure(url, status=exc.code,
                                    retry_after_s=_parse_retry_after(retry_after))
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("news fetch failed %s: %s", url, exc)
        return None
    if throttle is not None:
        throttle.record_success(url, data)
    return data


def _resolve_jobs(specs, symbol, enabled: set[str] | None = None):
    """(spec, url) for every enabled provider whose URL resolves for ``symbol``.

    When ``enabled`` is a set of provider names (from the event-providers config), a provider
    must also appear in that set to be included. When ``enabled`` is None the config filter is
    not applied — existing behavior is fully preserved.
    """
    jobs = []
    for spec in specs:
        if not spec.enabled:
            continue
        if enabled is not None and spec.name not in enabled:
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


def fetch_iter(specs, symbol, *, fetcher=fetch_feed, max_workers: int = 8,
              enabled: set[str] | None = None) -> Iterator[list[NewsItem]]:
    """Yield each feed's parsed items **as soon as that feed completes** (incremental render).

    Feeds still run concurrently; results arrive in completion order so the UI can paint the
    first feed without waiting for the slowest. Empty/dead feeds yield nothing.

    ``enabled``: when a set of provider names is supplied (from the event-providers config),
    only those providers are fetched. None = no config filter (existing behavior).
    """
    jobs = _resolve_jobs(specs, symbol, enabled=enabled)
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_fetch_parse, spec, url, fetcher) for spec, url in jobs]
        for fut in as_completed(futures):
            items = fut.result()
            if items:
                yield items


def fetch_all(specs, symbol, *, fetcher=fetch_feed, max_workers: int = 8,
              enabled: set[str] | None = None) -> list[NewsItem]:
    """Eager variant: every feed's items flattened into one list (one-shot callers/tests).

    ``enabled``: when a set of provider names is supplied (from the event-providers config),
    only those providers are fetched. None = no config filter (existing behavior).
    """
    items: list[NewsItem] = []
    for chunk in fetch_iter(specs, symbol, fetcher=fetcher, max_workers=max_workers, enabled=enabled):
        items.extend(chunk)
    return items
