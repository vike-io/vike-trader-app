"""Correctness tests for the pairs indicators module (Task 5):
ratio, spread, spread_zscore.
"""

import math

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _tail(lst, n=10):
    return [v for v in lst[-n:] if v is not None]


# ── ratio ─────────────────────────────────────────────────────────────────────

class TestRatio:
    def test_simple(self):
        from vike_trader_app.core.indicators.pairs import ratio
        a = [2.0, 4.0, 6.0]
        b = [1.0, 2.0, 3.0]
        out = ratio(a, b)
        assert out == pytest.approx([2.0, 2.0, 2.0])

    def test_length(self):
        from vike_trader_app.core.indicators.pairs import ratio
        a = [float(i + 1) for i in range(50)]
        b = [2.0] * 50
        assert len(ratio(a, b)) == 50

    def test_zero_denominator_gives_none(self):
        from vike_trader_app.core.indicators.pairs import ratio
        a = [1.0, 2.0, 3.0]
        b = [0.0, 2.0, 0.0]
        out = ratio(a, b)
        assert out[0] is None
        assert out[1] == pytest.approx(1.0)
        assert out[2] is None

    def test_all_finite_no_zeros(self):
        from vike_trader_app.core.indicators.pairs import ratio
        a = [100.0 + i for i in range(80)]
        b = [50.0 + i * 0.5 for i in range(80)]
        out = ratio(a, b)
        assert all(v is not None and math.isfinite(v) for v in out)


# ── spread ─────────────────────────────────────────────────────────────────────

class TestSpread:
    def test_arithmetic_mode(self):
        from vike_trader_app.core.indicators.pairs import spread
        a = [10.0, 20.0, 30.0]
        b = [5.0, 8.0, 12.0]
        out = spread(a, b, log=0)
        assert out == pytest.approx([5.0, 12.0, 18.0])

    def test_log_mode(self):
        from vike_trader_app.core.indicators.pairs import spread
        a = [math.exp(2.0), math.exp(3.0), math.exp(4.0)]
        b = [math.exp(1.0), math.exp(1.5), math.exp(2.5)]
        out = spread(a, b, log=1)
        assert out == pytest.approx([1.0, 1.5, 1.5], rel=1e-9)

    def test_length(self):
        from vike_trader_app.core.indicators.pairs import spread
        a = [float(i + 1) for i in range(50)]
        b = [float(i + 1) for i in range(50)]
        assert len(spread(a, b, log=0)) == 50

    def test_log_mode_nonpositive_gives_none(self):
        from vike_trader_app.core.indicators.pairs import spread
        # a[0]=0 → undefined log
        a = [0.0, 5.0]
        b = [1.0, -1.0]  # b[1] nonpositive
        out = spread(a, b, log=1)
        assert out[0] is None
        assert out[1] is None

    def test_arithmetic_no_guard_needed(self):
        """Arithmetic spread should work even with non-positive values."""
        from vike_trader_app.core.indicators.pairs import spread
        a = [-5.0, 0.0, 10.0]
        b = [2.0, 3.0, -4.0]
        out = spread(a, b, log=0)
        assert out == pytest.approx([-7.0, -3.0, 14.0])


# ── spread_zscore ─────────────────────────────────────────────────────────────

class TestSpreadZscore:
    def test_length(self):
        from vike_trader_app.core.indicators.pairs import spread_zscore
        a = [100.0 + math.sin(i / 5.0) for i in range(80)]
        b = [100.0 + math.cos(i / 5.0) for i in range(80)]
        assert len(spread_zscore(a, b, period=20)) == 80

    def test_warmup_none(self):
        from vike_trader_app.core.indicators.pairs import spread_zscore
        a = [float(i + 1) for i in range(50)]
        b = [float(i) for i in range(50)]
        out = spread_zscore(a, b, period=20)
        assert out[0] is None

    def test_mean_approx_zero_on_defined_tail(self):
        """Mean of z-scores on the defined tail should be approximately 0."""
        from vike_trader_app.core.indicators.pairs import spread_zscore
        a = [100.0 + math.sin(i / 5.0) for i in range(200)]
        b = [100.0 + math.cos(i / 7.0) for i in range(200)]
        out = spread_zscore(a, b, period=20)
        defined = [v for v in out if v is not None]
        assert defined, "no defined values"
        mean_val = sum(defined) / len(defined)
        assert abs(mean_val) < 1.5, f"mean of z-scores too far from 0: {mean_val}"

    def test_zero_stddev_gives_none(self):
        """Constant spread → stddev=0 → result is None (not crash)."""
        from vike_trader_app.core.indicators.pairs import spread_zscore
        # a-b is constant 1.0
        a = [float(i + 1) for i in range(50)]
        b = [float(i) for i in range(50)]
        out = spread_zscore(a, b, period=20)
        # All defined values should be None (zero std) or 0
        post_warmup = out[19:]
        for v in post_warmup:
            assert v is None or v == pytest.approx(0.0, abs=1e-9)

    def test_output_finite_on_varying_data(self):
        from vike_trader_app.core.indicators.pairs import spread_zscore
        a = [100.0 + math.sin(i / 3.0) for i in range(80)]
        b = [100.0 + math.cos(i / 4.0) for i in range(80)]
        out = spread_zscore(a, b, period=20)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── registration ──────────────────────────────────────────────────────────────

class TestPairsRegistration:
    def test_pairs_category_registered(self):
        from vike_trader_app.core.indicators import base
        # Trigger registration by importing ta (which imports pairs)
        import vike_trader_app.core.indicators.ta  # noqa: F401
        names = {s.name for s in base.list_indicators(category="pairs")}
        assert "ratio" in names, f"'ratio' not registered; pairs: {names}"
        assert "spread" in names, f"'spread' not registered; pairs: {names}"
        assert "spread_zscore" in names, f"'spread_zscore' not registered; pairs: {names}"

    def test_pairs_category_list(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        specs = base.list_indicators(category="pairs")
        assert len(specs) >= 3
        pair_names = [s.name for s in specs]
        print("Registered pairs indicators:", pair_names)

    def test_ratio_inputs(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        spec = base.get("ratio")
        assert spec.inputs == ["close", "benchmark"]
        assert spec.outputs == ["ratio"]

    def test_spread_inputs_and_params(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        spec = base.get("spread")
        assert spec.inputs == ["close", "benchmark"]
        assert len(spec.params) == 1
        assert spec.params[0].name == "log"

    def test_spread_zscore_inputs_and_params(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        spec = base.get("spread_zscore")
        assert spec.inputs == ["close", "benchmark"]
        assert len(spec.params) == 1
        assert spec.params[0].name == "period"
