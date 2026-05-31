"""Correctness tests for Tier A price transforms (avgprice/medprice/typprice/wclprice)."""

import pytest

from vike_trader_app.core.indicators.price import (
    avgprice,
    medprice,
    typprice,
    wclprice,
)


# ── 3-bar test series ─────────────────────────────────────────────────────────
OPEN  = [1.0, 2.0, 3.0]
HIGH  = [5.0, 6.0, 7.0]
LOW   = [0.5, 1.5, 2.5]
CLOSE = [4.0, 5.0, 6.0]


class TestAvgprice:
    def test_formula_exact(self):
        """avgprice = (O+H+L+C)/4 for each bar."""
        out = avgprice(OPEN, HIGH, LOW, CLOSE)
        assert len(out) == 3
        assert out[0] == pytest.approx((1.0 + 5.0 + 0.5 + 4.0) / 4)  # 2.625
        assert out[1] == pytest.approx((2.0 + 6.0 + 1.5 + 5.0) / 4)  # 3.625
        assert out[2] == pytest.approx((3.0 + 7.0 + 2.5 + 6.0) / 4)  # 4.625

    def test_no_warmup(self):
        """Price transforms have no warm-up; all bars are defined."""
        out = avgprice(OPEN, HIGH, LOW, CLOSE)
        assert all(v is not None for v in out)

    def test_length(self):
        n = 10
        opens  = [1.0] * n
        highs  = [2.0] * n
        lows   = [0.5] * n
        closes = [1.5] * n
        assert len(avgprice(opens, highs, lows, closes)) == n

    def test_constant_series(self):
        """All inputs equal → avgprice equals that constant."""
        out = avgprice([5.0] * 5, [5.0] * 5, [5.0] * 5, [5.0] * 5)
        assert all(v == pytest.approx(5.0) for v in out)


class TestMedprice:
    def test_formula_exact(self):
        """medprice = (H+L)/2 for each bar."""
        out = medprice(HIGH, LOW)
        assert len(out) == 3
        assert out[0] == pytest.approx((5.0 + 0.5) / 2)   # 2.75
        assert out[1] == pytest.approx((6.0 + 1.5) / 2)   # 3.75
        assert out[2] == pytest.approx((7.0 + 2.5) / 2)   # 4.75

    def test_no_warmup(self):
        out = medprice(HIGH, LOW)
        assert all(v is not None for v in out)

    def test_length(self):
        n = 10
        assert len(medprice([10.0] * n, [8.0] * n)) == n

    def test_constant_bands(self):
        out = medprice([12.0] * 5, [8.0] * 5)
        assert all(v == pytest.approx(10.0) for v in out)


class TestTypprice:
    def test_formula_exact(self):
        """typprice = (H+L+C)/3 for each bar."""
        out = typprice(HIGH, LOW, CLOSE)
        assert len(out) == 3
        assert out[0] == pytest.approx((5.0 + 0.5 + 4.0) / 3)   # 3.1666...
        assert out[1] == pytest.approx((6.0 + 1.5 + 5.0) / 3)   # 4.1666...
        assert out[2] == pytest.approx((7.0 + 2.5 + 6.0) / 3)   # 5.1666...

    def test_no_warmup(self):
        out = typprice(HIGH, LOW, CLOSE)
        assert all(v is not None for v in out)

    def test_length(self):
        n = 10
        assert len(typprice([11.0] * n, [9.0] * n, [10.0] * n)) == n

    def test_constant_series(self):
        out = typprice([7.0] * 5, [7.0] * 5, [7.0] * 5)
        assert all(v == pytest.approx(7.0) for v in out)


class TestWclprice:
    def test_formula_exact(self):
        """wclprice = (H+L+2C)/4 for each bar."""
        out = wclprice(HIGH, LOW, CLOSE)
        assert len(out) == 3
        assert out[0] == pytest.approx((5.0 + 0.5 + 2 * 4.0) / 4)   # 3.375
        assert out[1] == pytest.approx((6.0 + 1.5 + 2 * 5.0) / 4)   # 4.375
        assert out[2] == pytest.approx((7.0 + 2.5 + 2 * 6.0) / 4)   # 5.375

    def test_no_warmup(self):
        out = wclprice(HIGH, LOW, CLOSE)
        assert all(v is not None for v in out)

    def test_length(self):
        n = 10
        assert len(wclprice([11.0] * n, [9.0] * n, [10.0] * n)) == n

    def test_constant_series(self):
        out = wclprice([6.0] * 5, [6.0] * 5, [6.0] * 5)
        assert all(v == pytest.approx(6.0) for v in out)


# ── registry check ────────────────────────────────────────────────────────────

class TestPriceRegistration:
    def test_all_price_transforms_registered(self):
        from vike_trader_app.core.indicators import base
        names = {s.name for s in base.list_indicators(category="price")}
        expected = {"avgprice", "medprice", "typprice", "wclprice"}
        missing = expected - names
        assert not missing, f"Not registered: {missing}"
