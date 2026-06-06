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
