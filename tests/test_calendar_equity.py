from vike_trader_app.data.calendar.equity import (
    FinnhubEarnings, FinnhubIpo, FmpDividends,
    EarningsEvent, DividendEvent, IpoEvent,
)


def test_no_key_returns_empty():
    assert FinnhubEarnings(key=None, http=lambda u, **k: {}).fetch("2026-06-01", "2026-06-14") == []
    assert FinnhubIpo(key=None, http=lambda u, **k: {}).fetch("a", "b") == []
    assert FmpDividends(key=None, http=lambda u, **k: []).fetch("a", "b") == []


def test_finnhub_earnings_parses_live_shape():
    fake = {"earningsCalendar": [
        {"date": "2026-06-03", "symbol": "AAPL", "hour": "amc",
         "epsEstimate": 1.5, "epsActual": 1.62, "revenueEstimate": 9e10, "revenueActual": 9.4e10},
        {"date": "2026-06-04", "symbol": "MSFT", "hour": "bmo",
         "epsEstimate": 2.9, "epsActual": None, "revenueEstimate": 6e10, "revenueActual": None},
    ]}
    evs = FinnhubEarnings(key="k", http=lambda u, **k: fake).fetch("2026-06-01", "2026-06-14")
    assert len(evs) == 2 and isinstance(evs[0], EarningsEvent)
    assert evs[0].symbol == "AAPL" and evs[0].hour == "amc" and evs[0].eps_actual == 1.62
    assert evs[1].eps_actual is None     # not-yet-reported


def test_finnhub_ipo_parses_live_shape():
    fake = {"ipoCalendar": [
        {"date": "2026-06-10", "symbol": "NEWCO", "name": "New Co Inc", "exchange": "NASDAQ",
         "price": "18-20", "numberOfShares": 1.0e7, "status": "expected"},
    ]}
    evs = FinnhubIpo(key="k", http=lambda u, **k: fake).fetch("a", "b")
    assert len(evs) == 1 and isinstance(evs[0], IpoEvent)
    assert evs[0].symbol == "NEWCO" and evs[0].exchange == "NASDAQ" and evs[0].price == "18-20"


def test_fmp_dividends_parses_live_shape():
    fake = [
        {"symbol": "KO", "date": "2026-06-02", "paymentDate": "2026-07-01",
         "dividend": 0.485, "adjDividend": 0.485, "yield": 2.9, "frequency": "Quarterly"},
    ]
    evs = FmpDividends(key="k", http=lambda u, **k: fake).fetch("a", "b")
    assert len(evs) == 1 and isinstance(evs[0], DividendEvent)
    assert evs[0].symbol == "KO" and evs[0].amount == 0.485 and evs[0].yield_pct == 2.9
    assert evs[0].pay_date == "2026-07-01"


def test_providers_swallow_errors():
    def boom(url, **kw):
        raise RuntimeError("network down")
    assert FinnhubEarnings(key="k", http=boom).fetch("a", "b") == []
    assert FinnhubIpo(key="k", http=boom).fetch("a", "b") == []
    assert FmpDividends(key="k", http=boom).fetch("a", "b") == []
