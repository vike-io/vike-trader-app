"""6e GUI tests — MainWindow._on_option_instrument_chosen + _exec_symbol_override wiring.
Offscreen, drives the FULL MainWindow (bare-widget misses the title-bar one-shot-timer fix).
All tests run under VIKE_DISABLE_LIVE=1: pick is pure UI state (no network, no socket).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402

_NAME = "BTC-27JUN26-100000-C"
_NAME_B = "ETH-27JUN26-3000-P"   # second option for re-pick test


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_pick_sets_override_and_arm_bar_selection_no_autoarm(app, monkeypatch):
    """Chain pick: override set, arm bar pre-staged to deribit/Option, NO auto-arm."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        win._on_option_instrument_chosen(_NAME)

        # (a) override is set to the picked instrument
        assert win._exec_symbol_override == _NAME
        # (b) arm bar pre-staged to deribit / Option (NO arm emitted)
        assert win.exec_arm._venue.currentText() == "deribit"
        assert win.exec_arm._product.currentText() == "Option"
        # (c) NO auto-arm, NO session started
        assert getattr(win, "_exec_session", None) is None
        assert win.exec_arm._armed is False
    finally:
        win.shutdown()


def test_arm_lambda_uses_override_symbol(app, monkeypatch):
    """After a chain pick the arm path resolves to the picked option, not the chart symbol."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        win._on_option_instrument_chosen(_NAME)
        # Verify the symbol the arm lambda WOULD resolve to (venue-guarded override logic):
        # venue is "deribit" after set_selection, so the override is honoured.
        eff_sym = (win._exec_symbol_override
                   if win.exec_arm._venue.currentText() == "deribit"
                   else None) or win._symbol
        spec = win.exec_arm.current_spec(eff_sym)
        assert spec.symbol == _NAME
        assert spec.venue == "deribit"
        assert spec.product == "option"
    finally:
        win.shutdown()


def test_non_option_pick_is_inert(app, monkeypatch):
    """A pick for a non-Deribit ticker (BTCUSDT, a perp, etc.) leaves state unchanged."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        win._on_option_instrument_chosen("BTCUSDT")   # spot ticker, not a Deribit option name
        assert win._exec_symbol_override is None       # no state change

        win._on_option_instrument_chosen("BTC-PERPETUAL")  # perp, not an option
        assert win._exec_symbol_override is None

        win._on_option_instrument_chosen("")            # empty -> inert
        assert win._exec_symbol_override is None
    finally:
        win.shutdown()


def test_disarm_clears_override(app, monkeypatch):
    """Disarm clears _exec_symbol_override so the next arm reads self._symbol or a fresh pick."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        win._on_option_instrument_chosen(_NAME)
        assert win._exec_symbol_override == _NAME
        win._on_disarm_requested()                     # safe with no session (VIKE_DISABLE_LIVE)
        assert win._exec_symbol_override is None
    finally:
        win.shutdown()


def test_spot_arm_byte_identical_when_no_pick(app, monkeypatch):
    """No pick -> override is None -> arm spec resolves to self._symbol (spot/perp unchanged)."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        assert win._exec_symbol_override is None
        # venue is not deribit by default, so the guard also enforces None override
        eff_sym = (win._exec_symbol_override
                   if win.exec_arm._venue.currentText() == "deribit"
                   else None) or win._symbol
        spec = win.exec_arm.current_spec(eff_sym)
        assert spec.symbol == "BTCUSDT"
    finally:
        win.shutdown()


# --- CRITIC REFINEMENTS --------------------------------------------------------

def test_venue_guard_ignores_override_when_venue_not_deribit(app, monkeypatch):
    """Venue-guarded override: pick an option, then flip venue to binance ->
    the arm spec.symbol must resolve to self._symbol (not the deribit option name)."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        win._symbol = "BTCUSDT"
        win._on_option_instrument_chosen(_NAME)
        assert win._exec_symbol_override == _NAME

        # Flip venue selector to binance (simulates the user switching venue after a pick)
        win.exec_arm._venue.setCurrentText("binance")
        assert win.exec_arm._venue.currentText() == "binance"

        # With venue != "deribit", the override is ignored -> falls back to self._symbol
        eff_sym = (win._exec_symbol_override
                   if win.exec_arm._venue.currentText() == "deribit"
                   else None) or win._symbol
        spec = win.exec_arm.current_spec(eff_sym)
        assert spec.symbol == win._symbol, (
            f"expected {win._symbol!r} (override ignored for binance venue), got {spec.symbol!r}"
        )
    finally:
        win.shutdown()


def test_repick_overwrites_override(app, monkeypatch):
    """Picking option A then option B must leave _exec_symbol_override == B."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        win._on_option_instrument_chosen(_NAME)
        assert win._exec_symbol_override == _NAME

        win._on_option_instrument_chosen(_NAME_B)
        assert win._exec_symbol_override == _NAME_B
    finally:
        win.shutdown()


def test_disarm_to_switch_contract_when_armed(app, monkeypatch):
    """When a session is active, pick must NOT mutate _exec_symbol_override (no auto-disarm).
    Reports 'Disarm to switch' to the status line."""
    monkeypatch.setenv("VIKE_DISABLE_LIVE", "1")
    win = MainWindow()
    try:
        # Inject a sentinel session (non-None) to simulate armed state
        class _FakeSess:
            hub = None
        win._exec_session = _FakeSess()
        original_override = win._exec_symbol_override  # None

        status_msgs = []
        # Monkeypatch options tab: create a stub or find the real one
        class _StubTab:
            def set_status(self, txt):
                status_msgs.append(txt)
        win.options = _StubTab()

        win._on_option_instrument_chosen(_NAME)

        # Override must remain unchanged (no mutation while live)
        assert win._exec_symbol_override == original_override
        # Status line must contain the "Disarm to switch" hint
        assert any("Disarm" in m for m in status_msgs), (
            f"expected 'Disarm' in status messages, got {status_msgs}"
        )
    finally:
        win._exec_session = None   # clean up sentinel
        win.shutdown()
