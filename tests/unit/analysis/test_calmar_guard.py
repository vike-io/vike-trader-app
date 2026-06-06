"""calmar must not OverflowError on short/degenerate equity curves."""

from vike_trader_app.analysis.metrics import calmar


def test_calmar_short_curve_is_finite():
    assert calmar([10_000.0, 10_010.0]) == 0.0
    assert calmar([10_000.0, 9_990.0, 10_005.0]) != float("inf")
    assert isinstance(calmar([10_000.0]), float)
    assert calmar([]) == 0.0


def test_calmar_normal_curve_still_works():
    eq = [100.0 + i for i in range(2000)]
    val = calmar(eq)
    assert isinstance(val, float)   # must not raise
