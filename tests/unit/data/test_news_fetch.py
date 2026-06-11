import functools
import urllib.error
import urllib.request

from vike_trader_app.data.news import fetch as fetch_mod
from vike_trader_app.data.news.fetch import fetch_all, fetch_iter
from vike_trader_app.data.news.providers import ProviderSpec

RSS = b"""<rss version="2.0"><channel>
  <item><title>hello</title><link>https://x/1</link>
  <pubDate>Mon, 01 Jun 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_fetch_all_aggregates_enabled_specs():
    specs = [
        ProviderSpec("A", "crypto", "https://a/rss", "broad"),
        ProviderSpec("B", "crypto", "https://b/rss", "broad"),
        ProviderSpec("Off", "crypto", "https://c/rss", "broad", enabled=False),
    ]
    seen = {}

    def fake(url):
        seen[url] = seen.get(url, 0) + 1
        return RSS

    items = fetch_all(specs, None, fetcher=fake)
    assert len(items) == 2                       # A + B, "Off" skipped
    assert "https://c/rss" not in seen
    assert {it.source for it in items} == {"A", "B"}


def test_fetch_all_one_dead_feed_does_not_break_others():
    specs = [
        ProviderSpec("Good", "crypto", "https://good/rss", "broad"),
        ProviderSpec("Dead", "crypto", "https://dead/rss", "broad"),
    ]

    def fake(url):
        return RSS if "good" in url else None    # dead feed returns None

    items = fetch_all(specs, None, fetcher=fake)
    assert [it.source for it in items] == ["Good"]


def test_fetch_all_skips_symbol_feeds_when_no_symbol():
    specs = [ProviderSpec("Y", "stocks", "https://y/h?s={SYMBOL}", "symbol")]
    called = []
    fetch_all(specs, None, fetcher=lambda u: called.append(u))
    assert called == []                          # build_url → None → not fetched


def test_fetch_iter_yields_one_chunk_per_feed_incrementally():
    specs = [
        ProviderSpec("A", "crypto", "https://a/rss", "broad"),
        ProviderSpec("B", "crypto", "https://b/rss", "broad"),
    ]
    chunks = list(fetch_iter(specs, None, fetcher=lambda u: RSS))
    assert len(chunks) == 2                       # one chunk per feed (incremental, not one batch)
    assert all(len(c) == 1 for c in chunks)       # each chunk is that feed's parsed items
    assert {c[0].source for c in chunks} == {"A", "B"}


def test_fetch_iter_skips_dead_and_empty_feeds():
    specs = [
        ProviderSpec("Good", "crypto", "https://good/rss", "broad"),
        ProviderSpec("Dead", "crypto", "https://dead/rss", "broad"),
    ]
    chunks = list(fetch_iter(specs, None, fetcher=lambda u: RSS if "good" in u else None))
    assert len(chunks) == 1 and chunks[0][0].source == "Good"   # no empty chunk for the dead feed


def test_fetch_all_equals_iter_aggregate():
    specs = [
        ProviderSpec("A", "crypto", "https://a/rss", "broad"),
        ProviderSpec("B", "crypto", "https://b/rss", "broad"),
    ]
    flat = [it for chunk in fetch_iter(specs, None, fetcher=lambda u: RSS) for it in chunk]
    assert len(fetch_all(specs, None, fetcher=lambda u: RSS)) == len(flat) == 2


# --- W3-C: event-provider config filter ---

def test_fetch_all_disabled_in_config_is_skipped():
    """A provider disabled in the event-providers config is skipped even when spec.enabled=True."""
    specs = [
        ProviderSpec("A", "crypto", "https://a/rss", "broad"),
        ProviderSpec("B", "crypto", "https://b/rss", "broad"),
    ]
    # Only "A" is in the enabled set from the config
    items = fetch_all(specs, None, fetcher=lambda u: RSS, enabled={"A"})
    assert len(items) == 1
    assert items[0].source == "A"


def test_fetch_all_none_enabled_leaves_all_jobs():
    """enabled=None (no config file) → behavior is identical to before (both providers run)."""
    specs = [
        ProviderSpec("A", "crypto", "https://a/rss", "broad"),
        ProviderSpec("B", "crypto", "https://b/rss", "broad"),
    ]
    items = fetch_all(specs, None, fetcher=lambda u: RSS, enabled=None)
    assert {it.source for it in items} == {"A", "B"}


def test_fetch_all_spec_disabled_wins_even_if_in_enabled_set():
    """spec.enabled=False takes priority even when the provider name is in the config enabled set."""
    specs = [
        ProviderSpec("A", "crypto", "https://a/rss", "broad", enabled=False),
        ProviderSpec("B", "crypto", "https://b/rss", "broad"),
    ]
    items = fetch_all(specs, None, fetcher=lambda u: RSS, enabled={"A", "B"})
    assert len(items) == 1 and items[0].source == "B"


# --- per-URL rate-limit backoff + success-TTL cache (FeedThrottle) ---

URL = "https://feed.example/rss"


def _throttle(clk, **kw):
    """FeedThrottle on a fake list-cell clock (clk[0] = current monotonic seconds)."""
    kw.setdefault("backoff", 120.0)
    kw.setdefault("success_ttl", 30.0)
    kw.setdefault("max_backoff", 1800.0)
    return fetch_mod.FeedThrottle(now=lambda: clk[0], **kw)


def _http_error(url, code=429, headers=None):
    return urllib.error.HTTPError(url, code, "Too Many Requests", headers or {}, None)


class _FakeResponse:
    """Minimal context-manager stand-in for urlopen's response."""

    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_urlopen(monkeypatch, behavior):
    """Patch urlopen with a per-URL counting fake. ``behavior(url)`` → bytes or an Exception
    instance (raised). Returns the {url: call_count} dict."""
    calls = {}

    def fake(req, *args, **kwargs):
        url = req.full_url
        calls[url] = calls.get(url, 0) + 1
        result = behavior(url)
        if isinstance(result, Exception):
            raise result
        return _FakeResponse(result)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


def test_throttle_fresh_url_is_allowed_and_uncached():
    clk = [1000.0]
    t = _throttle(clk)
    assert t.allow(URL) is True
    assert t.cached(URL) is None


def test_throttle_429_blocks_url_for_base_backoff():
    clk = [1000.0]
    t = _throttle(clk)
    t.record_failure(URL, status=429)
    assert t.allow(URL) is False
    clk[0] = 1119.9
    assert t.allow(URL) is False                  # still inside the 120s base window
    clk[0] = 1120.0
    assert t.allow(URL) is True                   # window elapsed


def test_throttle_403_blocks_like_429():
    clk = [1000.0]
    t = _throttle(clk)
    t.record_failure(URL, status=403)
    assert t.allow(URL) is False
    clk[0] = 1120.0
    assert t.allow(URL) is True


def test_throttle_honors_retry_after_over_base():
    clk = [1000.0]
    t = _throttle(clk)
    t.record_failure(URL, status=429, retry_after_s=300.0)
    clk[0] = 1120.0
    assert t.allow(URL) is False                  # base backoff would already allow here
    clk[0] = 1299.0
    assert t.allow(URL) is False
    clk[0] = 1300.0
    assert t.allow(URL) is True


def test_throttle_non_rate_limit_status_is_noop():
    clk = [1000.0]
    t = _throttle(clk)
    t.record_failure(URL, status=404)             # dead feed → legacy behavior, no block
    assert t.allow(URL) is True
    t.record_failure(URL, status=None)            # unknown error → no block
    assert t.allow(URL) is True


def test_throttle_consecutive_429_doubles_backoff():
    clk = [1000.0]
    t = _throttle(clk)
    t.record_failure(URL, status=429)             # 1st failure → 120s window
    clk[0] = 1120.0
    t.record_failure(URL, status=429)             # 2nd consecutive → 240s window
    clk[0] = 1120.0 + 239.9
    assert t.allow(URL) is False
    clk[0] = 1120.0 + 240.0
    assert t.allow(URL) is True


def test_throttle_backoff_capped_at_max():
    clk = [1000.0]
    t = _throttle(clk)
    for _ in range(10):                           # 120 * 2**9 ≫ 1800 — must cap at max_backoff
        t.record_failure(URL, status=429)
    clk[0] = 1000.0 + 1799.9
    assert t.allow(URL) is False
    clk[0] = 1000.0 + 1800.0
    assert t.allow(URL) is True


def test_throttle_success_clears_failure_state():
    clk = [1000.0]
    t = _throttle(clk)
    t.record_failure(URL, status=429)
    t.record_failure(URL, status=429)             # 2 consecutive failures (240s window)
    t.record_success(URL, b"fresh")
    assert t.allow(URL) is True                   # block window cleared
    t.record_failure(URL, status=429)             # counter reset → base 120s again, not 480s
    clk[0] = 1119.9
    assert t.allow(URL) is False
    clk[0] = 1120.0
    assert t.allow(URL) is True


def test_throttle_cached_serves_within_ttl_then_expires():
    clk = [1000.0]
    t = _throttle(clk)
    t.record_success(URL, b"payload")
    clk[0] = 1029.0
    assert t.cached(URL) == b"payload"
    clk[0] = 1030.0
    assert t.cached(URL) is None                  # TTL boundary is exclusive


def test_throttle_success_ttl_zero_disables_cache():
    clk = [1000.0]
    t = _throttle(clk, success_ttl=0.0)
    t.record_success(URL, b"payload")
    assert t.cached(URL) is None


def test_throttle_state_is_per_url():
    clk = [1000.0]
    t = _throttle(clk)
    other = "https://other.example/rss"
    t.record_failure(URL, status=429)
    t.record_success(other, b"other-bytes")
    assert t.allow(URL) is False
    assert t.allow(other) is True
    assert t.cached(URL) is None
    assert t.cached(other) == b"other-bytes"


def test_fetch_feed_429_skips_url_until_window_elapses(monkeypatch):
    clk = [1000.0]
    t = _throttle(clk)
    calls = _install_urlopen(monkeypatch, lambda url: _http_error(url, 429, {"Retry-After": "90"}))
    assert fetch_mod.fetch_feed(URL, throttle=t) is None    # real attempt → 429
    assert calls[URL] == 1
    assert fetch_mod.fetch_feed(URL, throttle=t) is None    # blocked → no network call
    assert calls[URL] == 1
    clk[0] = 1090.0                                         # Retry-After window elapsed
    assert fetch_mod.fetch_feed(URL, throttle=t) is None
    assert calls[URL] == 2


def test_fetch_feed_uses_retry_after_header(monkeypatch):
    clk = [1000.0]
    t = _throttle(clk)
    calls = _install_urlopen(monkeypatch, lambda url: _http_error(url, 429, {"Retry-After": "300"}))
    assert fetch_mod.fetch_feed(URL, throttle=t) is None
    clk[0] = 1120.0                                         # base backoff would allow here
    assert fetch_mod.fetch_feed(URL, throttle=t) is None
    assert calls[URL] == 1                                  # Retry-After (300s) wins over base
    clk[0] = 1300.0
    fetch_mod.fetch_feed(URL, throttle=t)
    assert calls[URL] == 2


def test_fetch_feed_retry_after_non_numeric_falls_back_to_base(monkeypatch):
    clk = [1000.0]
    t = _throttle(clk)
    calls = _install_urlopen(
        monkeypatch,
        lambda url: _http_error(url, 429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
    )
    assert fetch_mod.fetch_feed(URL, throttle=t) is None
    clk[0] = 1119.9
    assert fetch_mod.fetch_feed(URL, throttle=t) is None
    assert calls[URL] == 1                                  # HTTP-date ignored → 120s base window
    clk[0] = 1120.0
    fetch_mod.fetch_feed(URL, throttle=t)
    assert calls[URL] == 2


def test_fetch_feed_success_serves_ttl_cache_without_refetch(monkeypatch):
    clk = [1000.0]
    t = _throttle(clk)
    calls = _install_urlopen(monkeypatch, lambda url: RSS)
    assert fetch_mod.fetch_feed(URL, throttle=t) == RSS
    assert calls[URL] == 1
    clk[0] = 1029.0
    assert fetch_mod.fetch_feed(URL, throttle=t) == RSS     # served from the success-TTL cache
    assert calls[URL] == 1
    clk[0] = 1030.0                                         # TTL expired → real refetch
    assert fetch_mod.fetch_feed(URL, throttle=t) == RSS
    assert calls[URL] == 2


def test_fetch_feed_throttle_none_preserves_legacy_behavior(monkeypatch):
    calls = _install_urlopen(monkeypatch, lambda url: _http_error(url, 429, {"Retry-After": "90"}))
    assert fetch_mod.fetch_feed(URL, throttle=None) is None
    assert fetch_mod.fetch_feed(URL, throttle=None) is None
    assert calls[URL] == 2                                  # no backoff: every call hits the network
    calls_ok = _install_urlopen(monkeypatch, lambda url: RSS)
    assert fetch_mod.fetch_feed(URL, throttle=None) == RSS
    assert fetch_mod.fetch_feed(URL, throttle=None) == RSS
    assert calls_ok[URL] == 2                               # no success-TTL cache either


def test_fetch_feed_timeout_error_not_throttled(monkeypatch):
    clk = [1000.0]
    t = _throttle(clk)
    calls = _install_urlopen(monkeypatch, lambda url: TimeoutError("timed out"))
    assert fetch_mod.fetch_feed(URL, throttle=t) is None
    assert t.allow(URL) is True                             # timeouts never open a block window
    assert fetch_mod.fetch_feed(URL, throttle=t) is None    # retried immediately
    assert calls[URL] == 2


def test_fetch_all_second_pass_skips_rate_limited_feed(monkeypatch):
    clk = [1000.0]
    t = _throttle(clk)
    specs = [
        ProviderSpec("Good", "crypto", "https://good/rss", "broad"),
        ProviderSpec("Limited", "crypto", "https://limited/rss", "broad"),
    ]

    def behavior(url):
        if "good" in url:
            return RSS
        return _http_error(url, 429, {"Retry-After": "90"})

    calls = _install_urlopen(monkeypatch, behavior)
    fetcher = functools.partial(fetch_mod.fetch_feed, throttle=t)

    items = fetch_all(specs, None, fetcher=fetcher)
    assert [it.source for it in items] == ["Good"]          # rate-limited feed swallowed as usual
    assert calls == {"https://good/rss": 1, "https://limited/rss": 1}

    clk[0] = 1060.0                                         # past Good's 30s TTL, inside Limited's 90s block
    items = fetch_all(specs, None, fetcher=fetcher)
    assert [it.source for it in items] == ["Good"]
    assert calls == {"https://good/rss": 2, "https://limited/rss": 1}   # Limited NOT re-hit
