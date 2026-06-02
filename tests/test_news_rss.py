from vike_trader_app.data.news.rss import parse_feed

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
  <item>
    <title>Bitcoin rallies past resistance</title>
    <link>https://news.example/btc</link>
    <description>&lt;p&gt;BTC up &lt;b&gt;5%&lt;/b&gt; today&lt;/p&gt;</description>
    <pubDate>Mon, 01 Jun 2026 12:04:00 GMT</pubDate>
  </item>
  <item>
    <title>No link item</title>
    <description>still parsed</description>
    <pubDate>Mon, 01 Jun 2026 11:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Ether ETF approved</title>
    <link href="https://news.example/eth"/>
    <summary>Regulator clears the product</summary>
    <updated>2026-06-01T10:30:00Z</updated>
  </entry>
</feed>"""


def test_parse_rss_strips_html_and_parses_rfc822_date():
    items = parse_feed(RSS, source="CoinDesk", market="crypto")
    assert len(items) == 2
    top = items[0]
    assert top.title == "Bitcoin rallies past resistance"
    assert top.url == "https://news.example/btc"
    assert top.summary == "BTC up 5% today"        # tags stripped, entities unescaped
    assert top.source == "CoinDesk" and top.market == "crypto"
    assert top.published_ms == 1780315440000        # 2026-06-01 12:04:00 UTC


def test_parse_atom_reads_href_link_and_iso_date():
    items = parse_feed(ATOM, source="Decrypt", market="crypto")
    assert len(items) == 1
    assert items[0].url == "https://news.example/eth"
    assert items[0].title == "Ether ETF approved"
    assert items[0].published_ms == 1780309800000   # 2026-06-01 10:30:00 UTC


def test_malformed_returns_empty():
    assert parse_feed(b"not xml at all", source="X", market="global") == []
    assert parse_feed(b"", source="X", market="global") == []


def test_missing_date_yields_zero():
    feed = b"""<rss version="2.0"><channel>
      <item><title>t</title><link>https://x/1</link></item>
    </channel></rss>"""
    items = parse_feed(feed, source="X", market="global")
    assert items[0].published_ms == 0
