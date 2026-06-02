from PySide6 import QtWidgets

from vike_trader_app.data.options.model import Expiry, OptionChain
from vike_trader_app.data.options.service import OptionsService


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _chain():
    exp = Expiry(date="2026-07-02", dte=30, label="02 Jul")
    return OptionChain("BTC", "crypto", 104000.0, exp, 1, "deribit", ())


class _StubProvider:
    name = "stub"
    asset_class = "crypto"

    def __init__(self, chain=None, exc=None):
        self._chain, self._exc = chain, exc

    def list_underlyings(self):
        return ["BTC"]

    def list_expiries(self, underlying):
        return [_chain().expiry]

    def fetch_chain(self, underlying, expiry, strikes=None):
        if self._exc:
            raise self._exc
        return self._chain


def test_fetch_now_emits_chain_ready():
    _app()
    svc = OptionsService(provider_factory=lambda u: _StubProvider(chain=_chain()))
    got = []
    svc.chainReady.connect(got.append)
    svc.set_underlying("BTC")
    svc.set_expiry(_chain().expiry)
    svc.fetch_now()
    assert len(got) == 1 and got[0].underlying == "BTC"


def test_fetch_now_routes_errors_to_failed():
    _app()
    svc = OptionsService(provider_factory=lambda u: _StubProvider(exc=RuntimeError("boom")))
    errs = []
    svc.failed.connect(errs.append)
    svc.set_underlying("BTC")
    svc.set_expiry(_chain().expiry)
    svc.fetch_now()
    assert errs and "boom" in errs[0]


def test_refresh_skips_when_busy():
    _app()
    svc = OptionsService(provider_factory=lambda u: _StubProvider(chain=_chain()))
    svc.set_underlying("BTC")
    svc.set_expiry(_chain().expiry)
    svc._busy = True  # simulate in-flight fetch
    assert svc.refresh() is False  # guarded, no new worker


def test_fetch_now_silent_when_not_configured():
    _app()
    svc = OptionsService()
    fired = []
    svc.chainReady.connect(fired.append)
    svc.failed.connect(fired.append)
    svc.fetch_now()  # prerequisites absent -> must not emit anything (no thread, no hang)
    assert fired == []
