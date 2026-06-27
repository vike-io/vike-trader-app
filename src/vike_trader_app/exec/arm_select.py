"""Qt-free pick->arm bridge: validate a picked options-chain instrument name is a tradable
Deribit option and project it onto the pre-arm selection (venue/product/symbol). Holds NO Qt
and NO exec logic — MainWindow's pick slot consumes the result to pre-stage the arm bar.

6e: this is the Qt-free seam between OptionsTab.instrumentChosen and MainWindow._on_option_instrument_chosen.
"""
from __future__ import annotations

from dataclasses import dataclass

from vike_trader_app.data.options.deribit import parse_instrument_name


@dataclass(frozen=True)
class ExecArmSelection:
    venue: str
    product: str
    symbol: str


def pick_to_arm_selection(instrument_name: str | None) -> ExecArmSelection | None:
    """Validate a picked options-chain instrument name is a tradable Deribit option and return the
    pre-arm selection (venue/product/symbol), or None if it is not (yfinance/equity/garbage/None).
    Pure: NO Qt, NO network — uses data.options.deribit.parse_instrument_name for validity only."""
    if not instrument_name:
        return None
    if parse_instrument_name(instrument_name) is None:   # not a Deribit option name
        return None
    return ExecArmSelection(venue="deribit", product="Option", symbol=instrument_name)
