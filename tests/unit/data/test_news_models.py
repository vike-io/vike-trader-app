from vike_trader_app.data.news.models import NewsItem, NewsFilter, make_id


def test_make_id_prefers_url_and_is_stable():
    a = make_id("https://x/1", "Title", "CoinDesk")
    b = make_id("https://x/1", "Different title", "Other")
    assert a == b                      # id keyed on url when present
    assert len(a) == 16


def test_make_id_falls_back_to_title_source_when_no_url():
    a = make_id("", "Title", "CoinDesk")
    b = make_id("", "Title", "CoinDesk")
    c = make_id("", "Title", "Decrypt")
    assert a == b and a != c           # stable; source disambiguates


def test_newsitem_defaults():
    it = NewsItem(id="1", title="t", url="u", summary="s",
                  source="CoinDesk", market="crypto", published_ms=10)
    assert it.symbols == ()


def test_newsfilter_defaults():
    f = NewsFilter()
    assert f.market is None and f.providers == frozenset() and f.symbol is None and f.query == ""
