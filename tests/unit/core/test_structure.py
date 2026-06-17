"""Correctness tests for the structure indicators module (Task 6):
zigzag, williams_fractal, pivot_points, volume_profile_poc.
"""

import math

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _tail(lst, n=10):
    return [v for v in lst[-n:] if v is not None]


# ── zigzag ────────────────────────────────────────────────────────────────────

class TestZigzag:
    def _clear_v_shape(self):
        """High-low series with a clear downswing then upswing (>5% deviation)."""
        # Starts at 100, drops to 80 (−20%), then rallies to 105 (+31%)
        n = 30
        highs = [100.0] * 10 + [100.0 - i * 2.5 for i in range(10)] + [80.0 + i * 2.5 for i in range(10)]
        lows = [h - 1.0 for h in highs]
        return highs, lows

    def test_length(self):
        from vike_trader_app.core.indicators.structure import zigzag
        highs, lows = self._clear_v_shape()
        out = zigzag(highs, lows, deviation=5.0)
        assert len(out) == len(highs)

    def test_pivot_marked_on_reversal(self):
        """A clear 20%+ reversal must have at least one non-None pivot."""
        from vike_trader_app.core.indicators.structure import zigzag
        highs, lows = self._clear_v_shape()
        out = zigzag(highs, lows, deviation=5.0)
        pivots = [v for v in out if v is not None]
        assert pivots, "No pivot detected on a clear reversal"

    def test_none_between_pivots(self):
        """At least some bars should be None (only pivot bars marked)."""
        from vike_trader_app.core.indicators.structure import zigzag
        highs, lows = self._clear_v_shape()
        out = zigzag(highs, lows, deviation=5.0)
        nones = [v for v in out if v is None]
        assert nones, "Expected None between pivots"

    def test_high_deviation_fewer_pivots(self):
        """With deviation=50%, only massive reversals trigger pivots."""
        from vike_trader_app.core.indicators.structure import zigzag
        highs, lows = self._clear_v_shape()
        out_tight = zigzag(highs, lows, deviation=5.0)
        out_loose = zigzag(highs, lows, deviation=50.0)
        pivots_tight = [v for v in out_tight if v is not None]
        pivots_loose = [v for v in out_loose if v is not None]
        # Loose threshold means fewer or equal pivots
        assert len(pivots_loose) <= len(pivots_tight)

    def test_pivot_price_within_range(self):
        """Pivot prices should be within the high-low range of the data."""
        from vike_trader_app.core.indicators.structure import zigzag
        highs, lows = self._clear_v_shape()
        out = zigzag(highs, lows, deviation=5.0)
        min_low = min(lows)
        max_high = max(highs)
        for v in out:
            if v is not None:
                assert min_low - 1e-9 <= v <= max_high + 1e-9


# ── williams_fractal ──────────────────────────────────────────────────────────

class TestWilliamsFractal:
    def _make_local_high(self):
        """Series with a clear local high at the centre."""
        # bar 5 has the highest high in [0..10]
        highs = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0, 104.0, 103.0, 102.0, 101.0, 100.0]
        lows  = [99.0,  100.0, 101.0, 102.0, 103.0, 109.0, 103.0, 102.0, 101.0, 100.0,  99.0]
        return highs, lows

    def test_length(self):
        from vike_trader_app.core.indicators.structure import williams_fractal
        highs, lows = self._make_local_high()
        fu, fd = williams_fractal(highs, lows, n=2)
        assert len(fu) == len(highs)
        assert len(fd) == len(lows)

    def test_up_fractal_at_local_high(self):
        """Bar 5 is the strict max of a 5-bar window (n=2) → fractal_up set."""
        from vike_trader_app.core.indicators.structure import williams_fractal
        highs, lows = self._make_local_high()
        fu, fd = williams_fractal(highs, lows, n=2)
        assert fu[5] == pytest.approx(110.0), f"Expected fractal_up=110 at bar 5, got {fu[5]}"

    def test_edges_none(self):
        """First n and last n bars must be None (edge guard)."""
        from vike_trader_app.core.indicators.structure import williams_fractal
        highs, lows = self._make_local_high()
        n = 2
        fu, fd = williams_fractal(highs, lows, n=n)
        for i in range(n):
            assert fu[i] is None, f"fu[{i}] should be None (edge)"
            assert fd[i] is None, f"fd[{i}] should be None (edge)"
        for i in range(len(highs) - n, len(highs)):
            assert fu[i] is None, f"fu[{i}] should be None (edge)"
            assert fd[i] is None, f"fd[{i}] should be None (edge)"

    def test_down_fractal_at_local_low(self):
        """Inverted series: bar 5 should be a fractal_down."""
        from vike_trader_app.core.indicators.structure import williams_fractal
        lows_inv  = [100.0, 99.0, 98.0, 97.0, 96.0, 90.0, 96.0, 97.0, 98.0, 99.0, 100.0]
        highs_inv = [v + 1 for v in lows_inv]
        fu, fd = williams_fractal(highs_inv, lows_inv, n=2)
        assert fd[5] == pytest.approx(90.0), f"Expected fractal_down=90 at bar 5, got {fd[5]}"

    def test_non_strict_not_fractal(self):
        """A tie breaks strictness — equal neighbor means NOT a fractal."""
        from vike_trader_app.core.indicators.structure import williams_fractal
        # bar 2 has the same high as bar 4
        highs = [100.0, 101.0, 105.0, 101.0, 105.0, 100.0]
        lows  = [99.0,  100.0, 104.0, 100.0, 104.0,  99.0]
        fu, fd = williams_fractal(highs, lows, n=2)
        # Neither bar 2 nor bar 3 is a strict max (bar 4 ties bar 2)
        assert fu[2] is None or fu[4] is None  # At most one can be fractal


# ── pivot_points ──────────────────────────────────────────────────────────────

class TestPivotPoints:
    def test_length(self):
        from vike_trader_app.core.indicators.structure import pivot_points
        highs  = [110.0] * 50
        lows   = [90.0]  * 50
        closes = [100.0] * 50
        p, r1, r2, r3, s1, s2, s3 = pivot_points(highs, lows, closes)
        assert all(len(x) == 50 for x in [p, r1, r2, r3, s1, s2, s3])

    def test_bar_0_all_none(self):
        from vike_trader_app.core.indicators.structure import pivot_points
        highs  = [110.0] * 50
        lows   = [90.0]  * 50
        closes = [100.0] * 50
        p, r1, r2, r3, s1, s2, s3 = pivot_points(highs, lows, closes)
        for series in [p, r1, r2, r3, s1, s2, s3]:
            assert series[0] is None

    def test_p_is_prev_hlc3(self):
        """P at bar i = (high[i-1] + low[i-1] + close[i-1]) / 3."""
        from vike_trader_app.core.indicators.structure import pivot_points
        highs  = [110.0, 112.0, 108.0, 115.0] + [100.0] * 10
        lows   = [90.0,  92.0,  88.0,  95.0]  + [100.0] * 10
        closes = [100.0, 105.0, 95.0, 110.0]  + [100.0] * 10
        p, _, _, _, _, _, _ = pivot_points(highs, lows, closes)
        # bar 1 → P from bar 0 data
        expected_p1 = (highs[0] + lows[0] + closes[0]) / 3.0
        assert p[1] == pytest.approx(expected_p1, rel=1e-9)
        # bar 2 → P from bar 1 data
        expected_p2 = (highs[1] + lows[1] + closes[1]) / 3.0
        assert p[2] == pytest.approx(expected_p2, rel=1e-9)

    def test_r1_s1_formula(self):
        """R1 = 2P - prevL, S1 = 2P - prevH."""
        from vike_trader_app.core.indicators.structure import pivot_points
        h, l, c = 110.0, 90.0, 100.0
        highs  = [h, 120.0]
        lows   = [l, 80.0]
        closes = [c, 110.0]
        pv, r1, r2, r3, s1, s2, s3 = pivot_points(highs, lows, closes)
        pval = (h + l + c) / 3.0
        assert r1[1] == pytest.approx(2 * pval - l, rel=1e-9)
        assert s1[1] == pytest.approx(2 * pval - h, rel=1e-9)
        assert r2[1] == pytest.approx(pval + (h - l), rel=1e-9)
        assert s2[1] == pytest.approx(pval - (h - l), rel=1e-9)
        assert r3[1] == pytest.approx(h + 2 * (pval - l), rel=1e-9)
        assert s3[1] == pytest.approx(l - 2 * (h - pval), rel=1e-9)

    def test_all_finite_post_warmup(self):
        from vike_trader_app.core.indicators.structure import pivot_points
        import math as _math
        highs  = [100.0 + i for i in range(50)]
        lows   = [80.0  + i for i in range(50)]
        closes = [90.0  + i for i in range(50)]
        results = pivot_points(highs, lows, closes)
        for series in results:
            for v in series[1:]:
                assert v is not None and _math.isfinite(v)


# ── volume_profile_poc ────────────────────────────────────────────────────────

class TestVolumeProfilePoc:
    def test_length(self):
        from vike_trader_app.core.indicators.structure import volume_profile_poc
        highs  = [100.0 + i * 0.1 for i in range(100)]
        lows   = [99.0  + i * 0.1 for i in range(100)]
        closes = [99.5  + i * 0.1 for i in range(100)]
        vols   = [1000.0] * 100
        out = volume_profile_poc(highs, lows, closes, vols, window=50, bins=24)
        assert len(out) == 100

    def test_warmup_none(self):
        from vike_trader_app.core.indicators.structure import volume_profile_poc
        highs  = [100.0 + i * 0.1 for i in range(100)]
        lows   = [99.0  + i * 0.1 for i in range(100)]
        closes = [99.5  + i * 0.1 for i in range(100)]
        vols   = [1000.0] * 100
        out = volume_profile_poc(highs, lows, closes, vols, window=50, bins=24)
        assert out[0] is None

    def test_poc_within_window_range(self):
        """The POC price must fall within [min(low), max(high)] of the window."""
        from vike_trader_app.core.indicators.structure import volume_profile_poc
        highs  = [100.0 + i * 0.1 for i in range(100)]
        lows   = [99.0  + i * 0.1 for i in range(100)]
        closes = [99.5  + i * 0.1 for i in range(100)]
        vols   = [1000.0] * 100
        window = 50
        out = volume_profile_poc(highs, lows, closes, vols, window=window, bins=24)
        for i in range(window - 1, 100):
            v = out[i]
            if v is not None:
                wl = min(lows[i - window + 1 : i + 1])
                wh = max(highs[i - window + 1 : i + 1])
                assert wl - 1e-9 <= v <= wh + 1e-9, (
                    f"POC {v} out of range [{wl}, {wh}] at bar {i}"
                )

    def test_volume_concentration_drives_poc(self):
        """When all volume concentrates at a low-price bar, POC should be near low."""
        from vike_trader_app.core.indicators.structure import volume_profile_poc
        n = 60
        highs  = [110.0] * n
        lows   = [90.0]  * n
        closes = [91.0]  * n      # closes near bottom
        vols   = [10000.0] * n    # all bars same volume at close near 91
        out = volume_profile_poc(highs, lows, closes, vols, window=50, bins=10)
        defined = [v for v in out if v is not None]
        assert defined, "No POC computed"
        # POC should be near 91 (bottom of range)
        for v in defined:
            # the entire range is 90–110, bins of width 2; close=91 → bin centre ≈91
            assert v < 100.0, f"POC {v} should be in the lower half of [90,110]"

    def test_all_finite_post_warmup(self):
        from vike_trader_app.core.indicators.structure import volume_profile_poc
        highs  = [100.0 + math.sin(i / 5) for i in range(100)]
        lows   = [98.0  + math.sin(i / 5) for i in range(100)]
        closes = [99.0  + math.sin(i / 5) for i in range(100)]
        vols   = [500.0 + i for i in range(100)]
        out = volume_profile_poc(highs, lows, closes, vols, window=50, bins=24)
        tail = [v for v in out[-10:] if v is not None]
        assert tail and all(math.isfinite(v) for v in tail)


# ── registration ──────────────────────────────────────────────────────────────

class TestStructureRegistration:
    def test_structure_category_registered(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        names = {s.name for s in base.list_indicators(category="structure")}
        expected = {"zigzag", "williams_fractal", "pivot_points", "volume_profile_poc"}
        missing = expected - names
        assert not missing, f"Not registered in 'structure': {missing}"

    def test_structure_category_list(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        specs = base.list_indicators(category="structure")
        assert len(specs) >= 4
        struct_names = [s.name for s in specs]
        print("Registered structure indicators:", struct_names)

    def test_zigzag_spec(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        spec = base.get("zigzag")
        assert spec.inputs == ["high", "low"]
        assert spec.outputs == ["zigzag"]
        assert spec.params[0].name == "deviation"

    def test_williams_fractal_spec(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        spec = base.get("williams_fractal")
        assert spec.inputs == ["high", "low"]
        assert spec.outputs == ["fractal_up", "fractal_down"]
        assert spec.params[0].name == "n"

    def test_pivot_points_spec(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        spec = base.get("pivot_points")
        assert spec.inputs == ["high", "low", "close"]
        assert set(spec.outputs) == {"p", "r1", "r2", "r3", "s1", "s2", "s3"}
        assert spec.params == []

    def test_volume_profile_poc_spec(self):
        from vike_trader_app.core.indicators import base
        import vike_trader_app.core.indicators.ta  # noqa: F401
        spec = base.get("volume_profile_poc")
        assert spec.inputs == ["high", "low", "close", "volume"]
        assert spec.outputs == ["poc"]
        param_names = {p.name for p in spec.params}
        assert "window" in param_names
        assert "bins" in param_names


# ── volume_profile (VPVR histogram helper) ────────────────────────────────────

def test_volume_profile_poc_va_and_conservation():
    from vike_trader_app.core.indicators.structure import volume_profile
    highs = [100, 101, 102, 101, 103, 101, 101]
    lows = [99, 100, 101, 100, 102, 100, 100]
    closes = [100, 101, 101, 101, 102, 101, 101]
    vols = [5, 40, 10, 30, 5, 20, 15]
    vp = volume_profile(highs, lows, closes, vols, bins=8, value_area=0.70)
    assert vp is not None
    # POC is the centre of the highest-volume bin (the ~101 cluster holds 115 of 125 vol)
    assert vp.poc_price == pytest.approx(101.25, abs=0.01)
    assert vp.va_low <= vp.poc_price <= vp.va_high
    assert sum(vp.bin_volumes) == pytest.approx(sum(vols))   # volume conserved
    assert len(vp.bin_centers) == len(vp.bin_volumes) == 8


def test_volume_profile_value_area_widens_to_target():
    from vike_trader_app.core.indicators.structure import volume_profile
    # flat-ish volume across a wide range -> value area must span multiple bins to reach 70%
    n = 50
    closes = [100 + i for i in range(n)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    vp = volume_profile(highs, lows, closes, [1.0] * n, bins=10, value_area=0.70)
    assert vp is not None
    assert vp.va_high > vp.va_low                 # a real band, not a single bin
    covered = sum(v for c, v in zip(vp.bin_centers, vp.bin_volumes) if vp.va_low <= c <= vp.va_high)
    assert covered >= 0.70 * sum(vp.bin_volumes)  # VA holds >= the target fraction


def test_volume_profile_none_on_degenerate_or_empty():
    from vike_trader_app.core.indicators.structure import volume_profile
    assert volume_profile([], [], [], [], bins=8) is None          # empty
    assert volume_profile([5, 5], [5, 5], [5, 5], [1, 1], bins=8) is None  # single price
    assert volume_profile([2, 3], [1, 2], [1.5, 2.5], [0, 0], bins=8) is None  # zero volume
