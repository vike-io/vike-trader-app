"""ReconcileSnapshot + VenueApiError live in crypto_client; binance.client re-exports the snapshot,
and BinanceApiError is a VenueApiError so the shared base's except-clause catches it."""

from vike_trader_app.exec.crypto_client import ReconcileSnapshot, VenueApiError
from vike_trader_app.exec.binance.client import ReconcileSnapshot as ReExported
from vike_trader_app.exec.binance.transport import BinanceApiError


def test_reconcile_snapshot_is_reexported_same_class():
    assert ReExported is ReconcileSnapshot


def test_reconcile_snapshot_defaults_empty():
    snap = ReconcileSnapshot()
    assert snap.positions == ()
    assert snap.open_orders == ()
    assert snap.position_avg_px == ()


def test_venue_api_error_carries_code_and_msg():
    err = VenueApiError(-2011, "Unknown order sent.")
    assert err.code == -2011
    assert err.msg == "Unknown order sent."


def test_binance_api_error_is_a_venue_api_error():
    assert issubclass(BinanceApiError, VenueApiError)
    err = BinanceApiError(-2010, "Filter failure")
    assert isinstance(err, VenueApiError)
    assert err.code == -2010
    assert err.msg == "Filter failure"
