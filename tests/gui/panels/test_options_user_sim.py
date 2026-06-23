"""Options-chain user-simulation: drive the REAL OptionsTab + OptionsService the way a user
would, fully offline.

A trader opens the Options space, the service "fetches" a chain (via an injected fake provider —
NO network), the expiry strip auto-selects the front contract, the grid populates with strikes /
bid-ask / greeks, and the user narrows the ±N-strikes window and flips to the Greeks view. We assert
OBSERVABLE widget state: rendered table rows/columns, populated bid/ask/greek cells, the centred ATM
marker, the strike-window symmetry, and a single front-expiry (0DTE) selection.

Network-free by construction: the OptionsService is given a fake `provider_factory`, and we drive
its SYNCHRONOUS `fetch_now()` (the test path) so everything stays on the main thread — no QThread,
no live feed, no modal dialogs.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless: must precede the PySide6 import

import pytest

pytest.importorskip("PySide6")  # skip cleanly in the non-UI CI job (no PySide6 there)

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.data.options import columns as C  # noqa: E402
from vike_trader_app.data.options.model import (  # noqa: E402
    Expiry,
    OptionChain,
    OptionQuote,
    StrikeRow,
    limit_strikes,
)
from vike_trader_app.data.options.service import OptionsService  # noqa: E402
from vike_trader_app.ui.options_tab import OptionsTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    # Module-scoped QApplication; the `app` fixture name auto-marks this file `gui` (tests/conftest).
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# --- synthetic, network-free chain data --------------------------------------
_SPOT = 100.0
# A symmetric strike ladder straddling the spot: 5 strikes below (90..98) and 5 at/above (100..108),
# so a ±N window has exactly N rows on each side of the ATM marker. Whole-dollar strikes keep the
# ATM split (first strike >= 100.0) at the 100 row — clean to reason about.
_STRIKES = [90.0, 92.0, 94.0, 96.0, 98.0, 100.0, 102.0, 104.0, 106.0, 108.0]


def _quote(strike: float, kind: str) -> OptionQuote:
    """A fully-populated quote (bid/ask/greeks) so every chain + greeks cell has real content."""
    moneyness = (_SPOT - strike) if kind == "C" else (strike - _SPOT)  # ITM-ness, signed
    intrinsic = max(moneyness, 0.0)
    return OptionQuote(
        strike=strike,
        type=kind,
        bid=round(intrinsic + 1.5, 2),
        ask=round(intrinsic + 1.9, 2),
        last=round(intrinsic + 1.7, 2),
        mark=round(intrinsic + 1.7, 2),
        iv=0.55,
        open_interest=100 + int(strike),
        volume=1000 - abs(int(strike) - int(_SPOT)) * 5,  # peaks at ATM, falls off in the wings
        delta=round(0.5 + moneyness / 100.0, 3),
        gamma=0.02,
        theta=-0.05,
        vega=0.12,
    )


def _full_chain(expiry: Expiry, *, source: str = "deribit") -> OptionChain:
    rows = tuple(
        StrikeRow(strike=k, call=_quote(k, "C"), put=_quote(k, "P")) for k in _STRIKES
    )
    return OptionChain("BTC", "crypto", _SPOT, expiry, 1, source, rows)


class _FakeProvider:
    """Stand-in OptionsProvider (satisfies the runtime_checkable Protocol) — returns the synthetic
    chain, never touches the network. Applies the ±N strike window itself, exactly like the real
    Deribit/yfinance providers do, so the service<->tab strikes plumbing is exercised end to end."""

    name = "fake"
    asset_class = "crypto"

    def __init__(self) -> None:
        # 0DTE front + a 7DTE and 30DTE back month — deliberately listed OUT of DTE order so we can
        # prove the strip selects the NEAREST (front/0DTE), not positional [0].
        self.expiries = [
            Expiry(date="2026-08-01", dte=30, label="01 Aug"),
            Expiry(date="2026-06-07", dte=0, label="0DTE"),
            Expiry(date="2026-06-14", dte=7, label="14 Jun"),
        ]
        self.fetch_calls: list[tuple[str, str, int | None]] = []

    def list_underlyings(self):
        return ["BTC", "ETH", "SOL"]

    def list_expiries(self, underlying):
        return list(self.expiries)

    def fetch_chain(self, underlying, expiry, strikes=None):
        self.fetch_calls.append((underlying, expiry.date, strikes))
        chain = _full_chain(expiry)
        return limit_strikes(chain, strikes)  # provider applies the ±N window (model helper)


def _build():
    """Wire a real OptionsTab to a real OptionsService backed by the fake provider, replaying the
    app's _wire_options signal graph but driving the SYNCHRONOUS fetch_now (main-thread, no QThread).
    Returns (tab, svc, provider)."""
    provider = _FakeProvider()
    svc = OptionsService(provider_factory=lambda _sym: provider)
    tab = OptionsTab()

    # Mirror MainWindow._wire_options, but use fetch_now() (sync) in place of the off-thread refresh.
    all_expiries: list[Expiry] = []

    svc.chainReady.connect(tab.set_chain)
    svc.failed.connect(tab.set_status)

    def _on_expiries(expiries):
        nonlocal all_expiries
        all_expiries = list(expiries)
        if not all_expiries:
            tab.no_data(tab.underlying.currentText())
            return
        days = tab.exp_range_days()
        within = [e for e in all_expiries if days is None or e.dte <= days]
        tab.set_expiries(within or all_expiries)  # strip auto-selects nearest -> expiryChanged

    svc.expiriesReady.connect(_on_expiries)

    def _load_underlying(sym):
        tab.begin_load(sym)
        svc.set_underlying(sym)
        svc.set_strikes(tab.strikes_value())
        # list_expiries is synchronous on the fake provider; emit straight through (no QThread).
        svc.expiriesReady.emit(provider.list_expiries(sym))

    def _select(iso):
        expiry = next((e for e in all_expiries if e.date == iso), None)
        if expiry is None:
            return
        svc.set_expiry(expiry)
        svc.set_strikes(tab.strikes_value())
        svc.fetch_now()  # SYNC fetch -> chainReady -> tab.set_chain (main thread)

    def _refresh():
        svc.set_strikes(tab.strikes_value())
        svc.fetch_now()

    tab.underlyingChanged.connect(_load_underlying)
    tab.expiryChanged.connect(_select)
    tab.refreshRequested.connect(_refresh)
    return tab, svc, provider


def _cols(table, label):
    return [c for c in range(table.columnCount())
            if table.horizontalHeaderItem(c).text() == label]


def _strike_col(table):
    return _cols(table, "Strike")[0]


def _strike_rows(tab):
    """Visible strike labels in row order, skipping the spanned ATM marker row."""
    t = tab.table
    sc = _strike_col(t)
    out = []
    for r in range(t.rowCount()):
        if t.columnSpan(r, 0) == t.columnCount():
            continue  # ATM marker spans the full width
        item = t.item(r, sc)
        if item is not None:
            out.append(item.text())
    return out


# --- the user journey --------------------------------------------------------
def test_open_options_select_symbol_expiry_and_populate_chain(app):
    """A user opens Options: pick the underlying -> the expiry strip auto-selects the front (0DTE)
    -> the chain grid fills with strikes, bid/ask and the ATM marker centred on spot."""
    tab, svc, provider = _build()

    # User picks a symbol (default underlying is BTC). Drive the real signal a combo click emits.
    tab.underlyingChanged.emit("BTC")
    app.processEvents()

    # The strip auto-selected the NEAREST expiry — 0DTE — even though it was listed second.
    assert tab.expiry_strip.current() == "2026-06-07"
    # ... and that single front-expiry fetch happened (one fetch, for 0DTE), network-free.
    assert provider.fetch_calls and provider.fetch_calls[-1][1] == "2026-06-07"
    assert svc._expiry.dte == 0  # the selected expiry IS the 0DTE/front contract

    t = tab.table
    # Chain view by default: calls + [Strike, IV] + puts columns.
    assert t.columnCount() == 2 * len(C.CHAIN_FIELDS) + 2
    for label in ("Bid", "Ask", "Vol", "Strike", "IV"):   # "Volume" header shortened to "Vol"
        assert _cols(t, label), f"missing chain column {label}"

    # Strikes populated (10 strikes), plus the spanned ATM marker row -> 11 visible rows.
    assert _strike_rows(tab) == ["90", "92", "94", "96", "98", "100", "102", "104", "106", "108"]
    assert t.rowCount() == len(_STRIKES) + 1  # +1 ATM marker

    # The ATM marker spans the full table and sits at the spot split (first strike >= 100 == "100").
    atm = next(r for r in range(t.rowCount()) if t.columnSpan(r, 0) == t.columnCount())
    assert tab._bar.atm_row == atm
    assert "100.00" in t.item(atm, 0).text() and "BTC" in t.item(atm, 0).text()

    # A real bid/ask actually rendered on a strike row (not the em-dash placeholder).
    bid_col = C.CHAIN_FIELDS.index("bid")  # calls side is reversed; locate via field->col map
    call_bid_col = next(c for c, (f, side) in tab._col_field.items() if f == "bid" and side == "C")
    call_ask_col = next(c for c, (f, side) in tab._col_field.items() if f == "ask" and side == "C")
    row0_bid = t.item(0, call_bid_col).text()
    row0_ask = t.item(0, call_ask_col).text()
    assert row0_bid not in ("", "—") and row0_ask not in ("", "—")
    assert bid_col >= 0  # sanity on the field set


def test_change_strikes_window_is_symmetric_around_atm(app):
    """User narrows the ±N-strikes window: assert the visible strike count + symmetry (N rows each
    side of the ATM band), driving the real refreshRequested signal the combo fires."""
    tab, svc, provider = _build()
    tab.underlyingChanged.emit("BTC")
    app.processEvents()

    # Default window is "±12 strikes" -> wider than our 10-strike ladder, so ALL 10 show.
    assert tab.strikes_value() == 12
    assert len(_strike_rows(tab)) == len(_STRIKES)

    # User selects "±3 strikes" and the combo fires refreshRequested (we emit the real signal).
    tab.strikes.setCurrentText("±3 strikes")
    assert tab.strikes_value() == 3
    tab.refreshRequested.emit()
    app.processEvents()

    visible = _strike_rows(tab)
    # ±3 around spot 100: 3 below (94,96,98) + 3 at/above (100,102,104) = 6 strikes, symmetric.
    assert len(visible) == 6
    assert visible == ["94", "96", "98", "100", "102", "104"]
    below = [s for s in visible if float(s) < _SPOT]
    at_or_above = [s for s in visible if float(s) >= _SPOT]
    assert len(below) == 3 and len(at_or_above) == 3  # symmetry: N each side
    # The provider was asked for the ±3 window (the strikes plumbing reached the fetch).
    assert provider.fetch_calls[-1][2] == 3

    # The ATM marker is still centred between the two halves (it's at the split, row index 3).
    t = tab.table
    atm = next(r for r in range(t.rowCount()) if t.columnSpan(r, 0) == t.columnCount())
    assert atm == 3  # 3 call rows above it, then the marker, then 3 put rows

    # Widen back to ±6 -> 6 each side, but our ladder only has 5 below spot, so it clamps to 5+5.
    tab.strikes.setCurrentText("±6 strikes")
    tab.refreshRequested.emit()
    app.processEvents()
    assert len(_strike_rows(tab)) == len(_STRIKES)  # all 10 again (window exceeds the ladder)


def test_switch_to_back_expiry_then_greeks_view(app):
    """User clicks a back-month expiry pill (real strip click) then flips to the Greeks view —
    assert the active expiry switched and the greek columns + cells populate."""
    tab, svc, provider = _build()
    tab.underlyingChanged.emit("BTC")
    app.processEvents()
    assert tab.expiry_strip.current() == "2026-06-07"  # front 0DTE first

    # User clicks the 30DTE pill — drive the REAL QToolButton click in the strip.
    tab.expiry_strip._buttons["2026-08-01"].click()
    app.processEvents()
    assert tab.expiry_strip.current() == "2026-08-01"
    assert svc._expiry.date == "2026-08-01" and svc._expiry.dte == 30
    assert provider.fetch_calls[-1][1] == "2026-08-01"  # fetched the chosen back-month

    # The chain still rendered for the new expiry.
    assert tab.status_label.text().startswith("BTC")
    assert "01 Aug" in tab.status_label.text()

    # User flips to the Greeks view (real combo activation path).
    tab.view_toggle.setCurrentText("Greeks")
    tab._on_view_changed()
    app.processEvents()
    t = tab.table
    assert t.columnCount() == 2 * len(C.GREEKS_FIELDS) + 2
    for label in ("Δ", "Γ", "Θ", "V", "OI", "Strike", "IV"):
        assert _cols(t, label), f"missing greeks column {label}"

    # A real greek value rendered (delta formatted to 3dp), not the em-dash.
    delta_col = next(c for c, (f, side) in tab._col_field.items() if f == "delta" and side == "C")
    delta_txt = t.item(0, delta_col).text()
    assert delta_txt not in ("", "—")
    assert "." in delta_txt  # numeric greek, e.g. "0.600"


def test_no_modal_on_failed_fetch(app):
    """A provider failure must surface to the status line, NOT a modal dialog (headless-safe)."""
    tab, svc, provider = _build()

    def _boom(_underlying, _expiry, _strikes=None):
        raise RuntimeError("exchange down")

    provider.fetch_chain = _boom  # type: ignore[assignment]
    tab.underlyingChanged.emit("BTC")
    app.processEvents()  # expiry strip auto-selects -> _select -> fetch_now -> failed -> set_status

    # No exception escaped; the error landed on the status label (the chain stays empty/cleared).
    assert "fake" in tab.status_label.text() or "exchange down" in tab.status_label.text()


def test_empty_expiries_shows_no_data_not_stale_chain(app):
    """If a symbol returns no expiries, the tab shows a 'no data' status instead of a stale grid."""
    tab, svc, provider = _build()
    # First load a real chain so there IS a grid to potentially go stale.
    tab.underlyingChanged.emit("BTC")
    app.processEvents()
    assert tab.table.rowCount() > 0

    # Now the provider returns no expiries for the next symbol.
    provider.expiries = []
    tab.underlyingChanged.emit("ETH")
    app.processEvents()

    assert tab.table.rowCount() == 0  # stale chain cleared
    assert "No options data" in tab.status_label.text()
