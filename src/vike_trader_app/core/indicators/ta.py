"""Back-compat shim for the original indicator import path.

The 17 founding indicators now live in category modules under this package (overlap/momentum/
volume/volatility) and self-register in the indicator registry. This module re-exports them so
existing imports — ``from vike_trader_app.core.indicators.ta import sma`` — keep working, and it
hosts the small ``expand`` helper and the optional ``from_talib`` bridge.
"""

from .momentum import adx, cci, macd, roc, rsi, stochastic, williams_r
from .overlap import ema, sma, wma
from .price import avgprice, medprice, typprice, wclprice  # registers price transforms
from .statistics import (  # registers statistics indicators
    linearreg,
    linearreg_slope,
    linearreg_angle,
    linearreg_intercept,
    tsf,
    var,
    beta,
    correl,
    zscore,
    skew,
    kurtosis,
    mad,
)
from .volatility import atr, bollinger, donchian, keltner, true_range
from .volume import obv, vwap

__all__ = [
    "sma", "ema", "wma", "rsi", "macd", "stochastic", "cci", "williams_r", "roc", "adx",
    "obv", "vwap", "atr", "true_range", "bollinger", "keltner", "donchian",
    "avgprice", "medprice", "typprice", "wclprice",
    "linearreg", "linearreg_slope", "linearreg_angle", "linearreg_intercept",
    "tsf", "var", "beta", "correl", "zscore", "skew", "kurtosis", "mad",
    "expand", "from_talib",
]


def expand(fn, values, periods):
    """Indicator factory: run ``fn(values, p)`` for each ``p`` -> ``{p: result}``."""
    return {p: fn(values, p) for p in periods}


def from_talib(name: str, *args, **kwargs):  # pragma: no cover - optional bridge
    """Optional bridge to TA-Lib for any function not shipped natively."""
    try:
        import talib
    except ImportError as exc:
        raise RuntimeError(
            "TA-Lib is not installed. vike-trader-app's native indicators cover the common set; "
            "install TA-Lib to bridge the rest: pip install TA-Lib"
        ) from exc
    return getattr(talib, name)(*args, **kwargs)
