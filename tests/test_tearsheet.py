"""HTML tearsheet tests."""

from vike_trader_app.analysis.tearsheet import write_tearsheet_html
from vike_trader_app.core.engine import Result
from vike_trader_app.core.model import Trade


def _result():
    eq = [10_000.0, 10_050.0, 9_900.0, 10_120.0]
    trades = [Trade(entry_price=100, exit_price=102, size=1, pnl=2.0, fees=0.1, entry_ts=0, exit_ts=60_000)]
    return Result(trades=trades, equity_curve=eq, final_equity=10_120.0)


def test_tearsheet_is_self_contained_html(tmp_path):
    path = write_tearsheet_html(tmp_path / "report.html", _result(), title="BTCUSDT 1m")
    html = path.read_text()
    assert html.startswith("<!doctype html>")
    assert "BTCUSDT 1m" in html
    assert "<svg" in html              # inline equity/drawdown charts
    assert "Sharpe" in html            # stats table
    assert "10120" in html.replace(",", "")  # final equity rendered
    assert "<table" in html            # trade log table
    assert "Rolling Sharpe" in html    # richer tearsheet sections
    assert "Return Distribution" in html
    assert html.count("<svg") >= 4     # equity + drawdown + rolling sharpe + histogram
