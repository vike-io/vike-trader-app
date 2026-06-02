from vike_trader_app.data.news.fetch import fetch_all
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
