from vike_trader_app.data.news.providers import (
    PROVIDERS, ProviderSpec, build_url, normalize,
)


def test_registry_nonempty_and_kinds_valid():
    assert len(PROVIDERS) >= 10
    assert {p.kind for p in PROVIDERS} <= {"broad", "symbol"}
    assert {p.market for p in PROVIDERS} <= {"crypto", "forex", "stocks", "global"}


def test_dailyfx_dropped_for_working_forex_sources():
    names = {p.name for p in PROVIDERS}
    assert "DailyFX" not in names                       # Akamai-blocked, never delivered
    assert {"FXEmpire", "Investing.com FX"} <= names     # verified-clean replacements
    forex = [p for p in PROVIDERS if p.market == "forex"]
    assert len(forex) >= 4 and all(p.kind == "broad" for p in forex)


def test_normalize_crypto_pair():
    n = normalize("BTCUSDT")
    assert n.base == "BTC" and n.ticker == "BTC-USD"
    assert "Bitcoin" in n.query


def test_normalize_forex_pair():
    n = normalize("EURUSD")
    assert n.query == "EUR/USD" and n.base == "EUR"


def test_normalize_equity_passthrough():
    n = normalize("AAPL")
    assert n.ticker == "AAPL" and n.query == "AAPL" and n.base == "AAPL"


def test_build_url_broad_unchanged():
    spec = ProviderSpec("X", "crypto", "https://x/rss", "broad")
    assert build_url(spec, "BTCUSDT") == "https://x/rss"


def test_build_url_symbol_templates_and_quotes():
    sym = ProviderSpec("Yahoo", "stocks", "https://y/h?s={SYMBOL}", "symbol")
    assert build_url(sym, "BTCUSDT") == "https://y/h?s=BTC-USD"
    q = ProviderSpec("GNews", "global", "https://g/search?q={QUERY}", "symbol")
    assert build_url(q, "EURUSD") == "https://g/search?q=EUR%2FUSD"   # url-quoted


def test_build_url_symbol_without_symbol_is_none():
    sym = ProviderSpec("Yahoo", "stocks", "https://y/h?s={SYMBOL}", "symbol")
    assert build_url(sym, None) is None
