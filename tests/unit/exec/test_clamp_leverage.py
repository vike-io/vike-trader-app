import pytest
from vike_trader_app.exec.risk import clamp_leverage


@pytest.mark.parametrize("req,cap,out", [
    (10.0, 5.0, 5.0),     # clamped down
    (3.0, 5.0, 3.0),      # under cap, unchanged
    (10.0, None, 10.0),   # no cap
    (0.0, 5.0, 1.0),      # floored at 1
    (7.0, 0.0, 1.0),      # zero cap floors to 1
])
def test_clamp_leverage(req, cap, out):
    assert clamp_leverage(req, cap) == out
