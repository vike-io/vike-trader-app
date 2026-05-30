"""Optional interactive (Plotly) HTML tearsheet."""

import builtins

import pytest

from vike_trader_app.analysis.interactive import write_interactive_html
from vike_trader_app.core.engine import Result
from vike_trader_app.core.model import Trade


def _result():
    eq = [10_000.0, 10_050.0, 9_900.0, 10_120.0]
    trades = [Trade(entry_price=100, exit_price=102, size=1, pnl=2.0, fees=0.1, entry_ts=0, exit_ts=60_000)]
    return Result(trades=trades, equity_curve=eq, final_equity=10_120.0)


def test_interactive_html_is_self_contained_plotly(tmp_path):
    pytest.importorskip("plotly")
    path = write_interactive_html(tmp_path / "i.html", _result(), title="BTC interactive")
    html = path.read_text(encoding="utf-8")
    assert "BTC interactive" in html
    assert "Plotly" in html or "plotly" in html  # the embedded interactive lib
    assert len(html) > 10_000  # self-contained (inline plotly.js)


def test_interactive_raises_without_extra(monkeypatch):
    real_import = builtins.__import__

    def _block(name, *a, **k):
        if name.split(".")[0] == "plotly":
            raise ImportError("simulated missing plotly")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _block)
    with pytest.raises(RuntimeError, match="vike_trader_app\\[viz\\]"):
        write_interactive_html("x.html", _result())
