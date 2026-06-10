"""Unit tests for the Qt-free session-persistence seam (ui/session.py).

No QApplication anywhere here: SessionState round-trips plain dicts/JSON, and the indicator
(de)hydration is exercised against duck-typed fakes of PriceChart / _Indicator.
"""

import json

from vike_trader_app.ui.session import (
    SessionState,
    apply_indicator_states,
    indicator_states,
    load_session,
    save_session,
)


# --- SessionState (de)serialization ---------------------------------------------------------


def test_state_roundtrip():
    state = SessionState(symbol="ETHUSDT", interval="4h", space=3, geometry_hex="01ff",
                         maximized=False, panels={"market": True, "trades": False},
                         chart_indicators=[{"name": "sma", "params": {"length": 20}}])
    again = SessionState.from_dict(state.to_dict())
    assert again == state


def test_to_dict_carries_version():
    assert SessionState().to_dict()["version"] == 1


def test_from_dict_rejects_non_dict():
    assert SessionState.from_dict(None) is None
    assert SessionState.from_dict("[]") is None
    assert SessionState.from_dict([1, 2]) is None


def test_from_dict_empty_yields_defaults():
    state = SessionState.from_dict({})
    assert state == SessionState()
    assert state.symbol == "BTCUSDT"
    assert state.space == 0


def test_from_dict_ignores_unknown_keys_and_bad_types():
    state = SessionState.from_dict({
        "symbol": "SOLUSDT",
        "space": "not-an-int",       # wrong type -> default kept
        "interval": 42,              # wrong type -> default kept
        "panels": {"market": True},
        "extra_future_field": "ignored",
    })
    assert state.symbol == "SOLUSDT"
    assert state.space == 0
    assert state.interval == "1m"
    assert state.panels == {"market": True}


# --- load/save ------------------------------------------------------------------------------


def test_load_missing_file_is_none(tmp_path):
    assert load_session(tmp_path / "nope.json") is None


def test_load_corrupt_file_is_none(tmp_path):
    p = tmp_path / "session.json"
    p.write_text("{not json!", encoding="utf-8")
    assert load_session(p) is None


def test_save_load_roundtrip_creates_parents(tmp_path):
    p = tmp_path / "deep" / "dir" / "session.json"
    state = SessionState(symbol="ETHUSDT", interval="1h", space=2, maximized=True,
                         panels={"trades": True})
    assert save_session(p, state)
    assert load_session(p) == state
    # the write is a real JSON file (atomic tmp got replaced, not left behind)
    assert json.loads(p.read_text(encoding="utf-8"))["symbol"] == "ETHUSDT"
    assert not list(p.parent.glob("*.tmp"))


def test_save_failure_returns_false(tmp_path):
    # a path whose parent is an existing FILE can't be created -> save must not raise
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    assert save_session(blocker / "session.json", SessionState()) is False


# --- indicator (de)hydration against fakes --------------------------------------------------


class _FakeInd:
    _seq = 0

    def __init__(self, name, params, kind="oscillator"):
        _FakeInd._seq += 1
        self.uid = _FakeInd._seq
        self.name = name
        self.params = dict(params)
        self.kind = kind
        self.visible = True
        self.intervals = None
        self.colors = ["#ff0000"]
        self.widths = [1]
        self.styles = ["solid"]
        self.source = "close"
        self.smooth_type = None
        self.smooth_len = 14
        self.smooth_color = "#f5a623"
        self.bands = [["upper", 70.0], ["lower", 30.0]]
        self.band_colors = ["#777777", "#777777"]


class _FakeChart:
    """Duck-typed PriceChart: records the add/_apply_edit/visibility calls hydration makes."""

    def __init__(self, fail_names=()):
        self._indicators = {}
        self._fail_names = set(fail_names)
        self.edits = []
        self.visibility_synced = []

    def add_indicator(self, name, params=None, benchmark=None):
        if name in self._fail_names:
            return None
        ind = _FakeInd(name, params or {})
        self._indicators[ind.uid] = ind
        return ind

    def _apply_edit(self, uid, params, colors, widths=None, styles=None,
                    intervals=None, source=None, bands=None):
        self.edits.append({"uid": uid, "params": params, "colors": colors, "widths": widths,
                           "styles": styles, "intervals": intervals, "source": source,
                           "bands": bands})

    def _sync_shown(self, ind):
        self.visibility_synced.append(ind.uid)

    def _apply_visibility(self, ind):
        pass


def test_indicator_states_serializes_and_skips_pairs():
    chart = _FakeChart()
    rsi = chart.add_indicator("rsi", {"length": 14})
    rsi.smooth_type = "ema"
    rsi.smooth_len = 9
    rsi.intervals = {"1h", "4h"}
    pairs = chart.add_indicator("ratio", {})
    pairs.kind = "pairs"

    states = indicator_states(chart)
    assert [s["name"] for s in states] == ["rsi"]  # pairs dropped
    st = states[0]
    assert st["params"] == {"length": 14}
    assert st["smooth_type"] == "ema" and st["smooth_len"] == 9
    assert st["intervals"] == ["1h", "4h"]  # sorted list (JSON-safe)
    assert st["bands"] == [["upper", 70.0], ["lower", 30.0]]
    assert st["visible"] is True


def test_states_json_roundtrip():
    chart = _FakeChart()
    chart.add_indicator("macd", {"fast": 12, "slow": 26})
    states = json.loads(json.dumps(indicator_states(chart)))
    assert states[0]["name"] == "macd"


def test_apply_restores_params_style_and_smoothing():
    src = _FakeChart()
    rsi = src.add_indicator("rsi", {"length": 21})
    rsi.colors = ["#00ff00"]
    rsi.smooth_type = "sma"
    rsi.smooth_len = 5
    rsi.intervals = {"1d"}
    states = indicator_states(src)

    dst = _FakeChart()
    assert apply_indicator_states(dst, states) == 1
    new = next(iter(dst._indicators.values()))
    assert new.params == {"length": 21}
    assert new.smooth_type == "sma" and new.smooth_len == 5
    edit = dst.edits[0]
    assert edit["uid"] == new.uid
    assert edit["colors"] == ["#00ff00"]
    assert edit["intervals"] == {"1d"}
    assert edit["source"] == "close"
    # bands rebuilt as (label, value, color) triples
    assert edit["bands"] == [("upper", 70.0, "#777777"), ("lower", 30.0, "#777777")]


def test_apply_restores_hidden_state():
    src = _FakeChart()
    src.add_indicator("rsi", {"length": 14}).visible = False
    dst = _FakeChart()
    apply_indicator_states(dst, indicator_states(src))
    new = next(iter(dst._indicators.values()))
    assert new.visible is False
    assert dst.visibility_synced == [new.uid]


def test_apply_skips_unknown_and_bad_entries():
    dst = _FakeChart(fail_names={"ghost"})
    states = [
        {"name": "ghost", "params": {}},      # add_indicator -> None (unknown / no bars)
        {"params": {}},                        # missing name -> skipped, not fatal
        {"name": "rsi", "params": {"length": 14}},
    ]
    assert apply_indicator_states(dst, states) == 1
    assert [i.name for i in dst._indicators.values()] == ["rsi"]


def test_apply_empty_or_none_is_zero():
    chart = _FakeChart()
    assert apply_indicator_states(chart, []) == 0
    assert apply_indicator_states(chart, None) == 0
