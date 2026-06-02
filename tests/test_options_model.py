from datetime import datetime, timezone

from vike_trader_app.data.options.model import (
    Expiry, OptionChain, OptionQuote, StrikeRow, limit_strikes, make_expiry,
)


def _ms(y, m, d, h=8):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def test_optionquote_is_frozen():
    q = OptionQuote(strike=100.0, type="C", bid=1.0, ask=1.2)
    assert q.strike == 100.0 and q.type == "C"
    try:
        q.strike = 1.0  # type: ignore[misc]
        assert False, "OptionQuote must be frozen"
    except Exception:
        pass


def test_make_expiry_dte_and_label():
    now = _ms(2026, 6, 2)
    e = make_expiry("2026-07-02", now)
    assert e.date == "2026-07-02"
    assert e.dte == 30
    assert e.label == "02 Jul"
    assert make_expiry("2026-06-02", now).label == "0DTE"


def test_limit_strikes_keeps_n_nearest_atm_sorted():
    rows = tuple(StrikeRow(strike=float(s)) for s in (80, 90, 100, 110, 120))
    chain = OptionChain(
        underlying="BTC", asset_class="crypto", underlying_price=104.0,
        expiry=make_expiry("2026-07-02", _ms(2026, 6, 2)), asof_ms=_ms(2026, 6, 2),
        source="deribit", rows=rows,
    )
    out = limit_strikes(chain, 3)
    assert [r.strike for r in out.rows] == [90.0, 100.0, 110.0]
    # None / oversized n is a no-op
    assert limit_strikes(chain, None).rows == rows
