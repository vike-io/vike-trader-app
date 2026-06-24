"""TDD: shared perp-reconcile hook seam on CryptoExecutionClient (Task 1 / perps 5b)."""
from __future__ import annotations

import pytest

from vike_trader_app.exec.crypto_client import CryptoExecutionClient, ReconcileSnapshot


class _SpotStub(CryptoExecutionClient):
    # minimal spot overrides so connect() runs the spot path
    def build_account_params(self): return {}
    def build_open_orders_params(self): return {}
    def build_ticker_params(self): return {}
    def iter_balances(self, r): return iter([{"asset": "BTC", "free": "1.0"}])
    def iter_open_orders(self, r): return iter([])
    def parse_mark_px(self, r): return 100.0
    def unwrap(self, r): return r


class _PerpStub(CryptoExecutionClient):
    PRODUCT = "perp"

    def reconcile_positions(self):
        return ReconcileSnapshot(positions=(("BTCUSDT", -0.5),),
                                 position_avg_px=(("BTCUSDT", 65000.0),),
                                 position_mark_px=(("BTCUSDT", 65100.0),))


def test_spot_product_uses_balance_connect():
    c = _SpotStub(bus=None, signer=object(), rest_base_url="x", symbol="BTCUSDT",
                  filters={}, base_asset="BTC",
                  transport=lambda *a, **k: {}, public_transport=lambda *a, **k: {})
    snap = c.connect()
    assert snap.positions == (("BTCUSDT", 1.0),)          # spot path still runs
    assert snap.position_mark_px == ()                    # spot leaves the new field empty


def test_perp_product_routes_to_reconcile_positions():
    c = _PerpStub(bus=None, signer=object(), rest_base_url="x", symbol="BTCUSDT",
                  filters={}, transport=lambda *a, **k: {}, public_transport=lambda *a, **k: {})
    snap = c.connect()
    assert snap.positions == (("BTCUSDT", -0.5),)         # SIGNED (short)
    assert snap.position_avg_px == (("BTCUSDT", 65000.0),)
    assert snap.position_mark_px == (("BTCUSDT", 65100.0),)


def test_base_reconcile_positions_raises():
    with pytest.raises(NotImplementedError):
        CryptoExecutionClient.reconcile_positions(object())
