import json

from vike_trader_app.data.calendar.equity import (
    FinnhubEarnings, FinnhubIpo, NasdaqIpo, Ipo, FmpDividends,
    EarningsEvent, DividendEvent, IpoEvent,
)


def _nasdaq_payload():
    """Shape of Nasdaq's /api/ipo/calendar response (upcoming nests under upcomingTable)."""
    return {"data": {
        "upcoming": {"upcomingTable": {"rows": [
            {"proposedTickerSymbol": "WHK", "companyName": "WhiteHawk Income Corp",
             "proposedExchange": "NYSE", "proposedSharePrice": "25.00-27.00",
             "sharesOffered": "6,925,000", "expectedPriceDate": "6/05/2026",
             "dollarValueOfSharesOffered": "$215,021,250"},
            {"proposedTickerSymbol": "LATE", "companyName": "Out Of Window Inc",
             "proposedExchange": "NASDAQ", "proposedSharePrice": "10.00",
             "sharesOffered": "1,000,000", "expectedPriceDate": "7/20/2026"},   # outside [frm,to]
        ]}},
        "priced": {"rows": [
            {"proposedTickerSymbol": "BIDWU", "companyName": "Tribeca Strategic Acquisition Corp.",
             "proposedExchange": "NASDAQ Global", "proposedSharePrice": "10.00",
             "sharesOffered": "14,000,000", "pricedDate": "6/03/2026"},
        ]},
        "filed": {"rows": []},
        "withdrawn": {"rows": []},
    }}


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


def test_nasdaq_ipo_parses_and_windows_live_shape():
    payload = _nasdaq_payload()
    evs = NasdaqIpo(http=lambda u, **k: payload).fetch("2026-06-01", "2026-06-07")
    byid = {e.symbol: e for e in evs}
    assert "LATE" not in byid                          # 7/20 row filtered out of the week window
    assert byid["WHK"].date == "2026-06-05" and byid["WHK"].exchange == "NYSE"
    assert byid["WHK"].price == "25.00-27.00" and byid["WHK"].shares == 6_925_000.0
    assert byid["WHK"].status == "upcoming"
    assert byid["BIDWU"].date == "2026-06-03" and byid["BIDWU"].status == "priced"  # priced section too


def test_nasdaq_ipo_swallows_errors():
    def boom(url, **kw):
        raise RuntimeError("network down")
    assert NasdaqIpo(http=boom).fetch("2026-06-01", "2026-06-07") == []


def test_ipo_prefers_nasdaq_then_falls_back_to_finnhub():
    nasdaq_payload = _nasdaq_payload()
    finnhub_payload = {"ipoCalendar": [
        {"date": "2026-06-04", "symbol": "FBONLY", "name": "Fallback Co", "exchange": "NYSE",
         "price": "12-14", "numberOfShares": 5.0e6, "status": "expected"},
    ]}

    def both(url, **kw):
        return finnhub_payload if "finnhub.io" in url else nasdaq_payload

    # Nasdaq has rows -> Finnhub is not used.
    evs = Ipo(key="k", http=both).fetch("2026-06-01", "2026-06-07")
    assert {e.symbol for e in evs} == {"WHK", "BIDWU"}

    # Nasdaq empty (out-of-window month) -> falls back to the Finnhub rows.
    def empty_nasdaq(url, **kw):
        return finnhub_payload if "finnhub.io" in url else {"data": {}}

    evs = Ipo(key="k", http=empty_nasdaq).fetch("2026-06-01", "2026-06-07")
    assert [e.symbol for e in evs] == ["FBONLY"]


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


def _isolate_profiles(monkeypatch, tmp_path):
    """Point the profile cache's BOTH seams (DB + legacy JSON) inside tmp_path."""
    import vike_trader_app.data.calendar.equity as eq
    monkeypatch.setattr(eq, "_PROFILE_CACHE", str(tmp_path / "calendar" / "profiles.json"))
    monkeypatch.setattr(eq, "_PROFILE_DB", str(tmp_path / "app.sqlite"))
    return eq


def test_fetch_earnings_enriched_adds_name_and_cap(monkeypatch, tmp_path):
    eq = _isolate_profiles(monkeypatch, tmp_path)

    def fake(url, **kw):
        if "calendar/earnings" in url:
            return {"earningsCalendar": [
                {"date": "2026-06-03", "symbol": "AAPL", "hour": "amc",
                 "epsEstimate": 1.5, "epsActual": 1.6, "revenueEstimate": 9e10, "revenueActual": 9.4e10},
                {"date": "2026-06-03", "symbol": "ZZZZ", "hour": "",   # uncovered -> not enriched
                 "epsEstimate": None, "epsActual": None, "revenueEstimate": None, "revenueActual": None},
            ]}
        if "profile2" in url:
            return {"name": "Apple Inc", "marketCapitalization": 3_500_000.0}
        return {}

    evs = eq.fetch_earnings_enriched("2026-06-01", "2026-06-07", key="k", http=fake)
    byid = {e.symbol: e for e in evs}
    assert byid["AAPL"].name == "Apple Inc" and byid["AAPL"].market_cap == 3_500_000.0
    assert byid["AAPL"].surprise is not None
    assert byid["ZZZZ"].name == "" and byid["ZZZZ"].market_cap is None


def test_profiles_cached_in_db_and_not_refetched(monkeypatch, tmp_path):
    eq = _isolate_profiles(monkeypatch, tmp_path)
    calls = {"n": 0}

    def fake(url, **kw):
        calls["n"] += 1
        return {"name": "Apple Inc", "marketCapitalization": 3_500_000.0}

    assert eq.profiles(["AAPL"], key="k", http=fake)["AAPL"]["name"] == "Apple Inc"
    assert eq.profiles(["AAPL"], key="k", http=fake)["AAPL"]["cap"] == 3_500_000.0
    assert calls["n"] == 1   # second call served from the calendar_profiles table


def test_profiles_migrates_legacy_json_blob(monkeypatch, tmp_path):
    eq = _isolate_profiles(monkeypatch, tmp_path)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    legacy = tmp_path / "calendar" / "profiles.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"AAPL": {"name": "Apple Inc", "cap": 3.5e6}}), encoding="utf-8")

    profs = eq.profiles(["AAPL"])    # no key -> served purely from the migrated cache
    assert profs["AAPL"] == {"name": "Apple Inc", "cap": 3.5e6}
    assert not legacy.exists()           # swept into the DB, file deleted
    assert not legacy.parent.exists()    # emptied legacy dir removed too
