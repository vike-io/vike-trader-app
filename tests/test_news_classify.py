"""Derived Category classifier, RSS <category> → tags parsing, and category/market filters."""
from vike_trader_app.data.news.aggregator import apply_filter
from vike_trader_app.data.news.classify import classify
from vike_trader_app.data.news.models import NewsFilter, NewsItem
from vike_trader_app.data.news.rss import parse_feed


def _item(title, *, tags=(), market="stocks"):
    return NewsItem(id=title, title=title, url="u", summary="", source="X", market=market,
                    published_ms=1, tags=tags)


def test_classify_buckets():
    assert classify(_item("Apple Q3 earnings beat estimates")) == "Earnings"
    assert classify(_item("Acme to acquire Beta in $2B deal")) == "M&A"
    assert classify(_item("Fed holds interest rate, signals cuts")) == "Macro"
    assert classify(_item("Bitcoin rallies above $70k")) == "Crypto"
    assert classify(_item("SEC files lawsuit against exchange")) == "Regulation"
    assert classify(_item("Crude oil jumps as OPEC cuts output")) == "Commodities"
    assert classify(_item("Nvidia unveils new AI chip")) == "Tech"
    assert classify(_item("Stocks drift sideways into the close")) == "Markets"   # catch-all
    assert classify(_item("Quiet day", tags=("Earnings",))) == "Earnings"          # tag informs bucket


def test_classify_avoids_substring_false_positives():
    # word-boundary matching: keywords must not fire inside unrelated longer words
    assert classify(_item("Fed announces emergency rate decision")) == "Macro"   # not M&A ('merge')
    assert classify(_item("Tech stocks emerge from slump")) != "M&A"             # 'merge' in 'emerge'
    assert classify(_item("Nonprofit launches climate fund")) != "Earnings"      # 'profit' in 'nonprofit'
    assert classify(_item("Urban planning reshapes the city")) != "Regulation"   # 'ban' in 'urban'
    assert classify(_item("Lebanon governor steps down")) != "Earnings"          # 'eps' in 'steps'
    assert classify(_item("Election results show a tight race")) != "Earnings"   # generic 'results'


def test_rss_parses_category_tags():
    xml = (b"<?xml version='1.0'?><rss><channel>"
           b"<item><title>X</title><link>http://x</link>"
           b"<category>Earnings</category><category domain='d'>US Stocks</category>"
           b"</item></channel></rss>")
    items = parse_feed(xml, source="S", market="stocks")
    assert items and items[0].tags == ("Earnings", "US Stocks")


def test_atom_category_term_becomes_tag():
    xml = (b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>"
           b"<entry><title>Y</title><link href='http://y'/>"
           b"<category term='Macro'/></entry></feed>")
    items = parse_feed(xml, source="S", market="global")
    assert items and items[0].tags == ("Macro",)


def test_category_filter_in_aggregator():
    items = [_item("Apple Q3 earnings beat"), _item("Bitcoin soars", market="crypto")]
    out = apply_filter(items, NewsFilter(categories=frozenset({"Earnings"})))
    assert [i.title for i in out] == ["Apple Q3 earnings beat"]


def test_markets_multiselect_filter():
    items = [_item("a", market="stocks"), _item("b", market="crypto"), _item("c", market="forex")]
    out = apply_filter(items, NewsFilter(markets=frozenset({"crypto", "forex"})))
    assert sorted(i.market for i in out) == ["crypto", "forex"]
