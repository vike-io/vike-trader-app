"""Every registered indicator computes on synthetic OHLCV and returns output aligned to input."""

import math

import pytest

from vike_trader_app.core.indicators import base


def _synth(n=120):
    closes = [100.0 + 10 * math.sin(i / 7) + (i % 5) for i in range(n)]
    return {
        "open": [c - 0.3 for c in closes],
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "volume": [1000.0 + (i % 9) * 10 for i in range(n)],
        "benchmark": [100.0 + 8 * math.sin(i / 6) for i in range(n)],
        "close2": [100.0 + 8 * math.sin(i / 6) for i in range(n)],
    }


@pytest.mark.parametrize("name", sorted(base.REGISTRY))
def test_every_indicator_computes_and_aligns(name):
    data = _synth()
    spec = base.get(name)
    if not all(k in data for k in spec.inputs):
        pytest.skip(f"{name} needs inputs {spec.inputs}")
    out = base.compute(name, data)
    n = len(data["close"])
    series = out if isinstance(out, tuple) else (out,)
    assert len(series) == len(spec.outputs), f"{name}: output count != declared outputs"
    for line in series:
        assert len(line) == n, f"{name}: output length {len(line)} != input length {n}"
        tail = [v for v in line[-10:] if v is not None]
        assert tail, f"{name}: no finite values in tail"
        assert all(isinstance(v, (int, float)) and not math.isnan(v) and not math.isinf(v)
                   for v in tail), f"{name}: non-finite tail values"
