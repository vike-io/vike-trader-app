from vike_trader_app.data.news.aggregator import merge, apply_filter
from vike_trader_app.data.news.models import NewsItem, NewsFilter


def _item(id, ms, *, market="crypto", source="CoinDesk", title="t", summary="s", symbols=()):
    return NewsItem(id=id, title=title, url=f"u/{id}", summary=summary,
                    source=source, market=market, published_ms=ms, symbols=symbols)


def test_merge_dedupes_by_id_and_sorts_desc():
    existing = [_item("a", 100), _item("b", 200)]
    incoming = [_item("b", 250), _item("c", 300)]      # "b" updated
    out = merge(existing, incoming)
    assert [it.id for it in out] == ["c", "b", "a"]    # newest first
    assert next(it for it in out if it.id == "b").published_ms == 250


def test_merge_caps_length():
    items = [_item(str(i), i) for i in range(10)]
    out = merge([], items, cap=3)
    assert len(out) == 3 and out[0].id == "9"


def test_filter_by_market():
    items = [_item("a", 1, market="crypto"), _item("b", 2, market="forex")]
    out = apply_filter(items, NewsFilter(market="forex"))
    assert [it.id for it in out] == ["b"]


def test_filter_by_provider():
    items = [_item("a", 1, source="CoinDesk"), _item("b", 2, source="CNBC")]
    out = apply_filter(items, NewsFilter(providers=frozenset({"CNBC"})))
    assert [it.id for it in out] == ["b"]


def test_filter_by_symbol_matches_tag_or_text():
    items = [
        _item("a", 1, symbols=("BTC",)),
        _item("b", 2, title="Bitcoin breaks out", symbols=()),
        _item("c", 3, title="Apple earnings", symbols=()),
    ]
    out = apply_filter(items, NewsFilter(symbol="BTCUSDT"))
    assert {it.id for it in out} == {"a", "b"}          # tag match + name/text match


def test_filter_by_query_substring():
    items = [_item("a", 1, title="ETF approved"), _item("b", 2, summary="rate cut")]
    out = apply_filter(items, NewsFilter(query="rate"))
    assert [it.id for it in out] == ["b"]
