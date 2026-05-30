"""Export trade markers as a TradingView Pine Script v5 indicator.

Produces a self-contained ``indicator`` script that draws up/down triangles at the
backtest's entry/exit bar timestamps (epoch ms, matching Pine's ``time``), so a run
can be eyeballed on a real TradingView chart.
"""


def to_pine(trades, title: str = "vike-trader-app") -> str:
    """Return Pine v5 source plotting entry/exit markers for ``trades``."""
    entries = ", ".join(str(t.entry_ts) for t in trades)
    exits = ", ".join(str(t.exit_ts) for t in trades)
    # array.from() with no args is invalid Pine; emit an explicitly-typed empty array.
    entry_arr = f"array.from({entries})" if trades else "array.new<int>()"
    exit_arr = f"array.from({exits})" if trades else "array.new<int>()"
    return f"""//@version=5
indicator("{title}", overlay=true)
var entries = {entry_arr}
var exits = {exit_arr}
isEntry = array.includes(entries, time)
isExit = array.includes(exits, time)
plotshape(isEntry, title="entry", style=shape.triangleup, location=location.belowbar, color=color.green, size=size.small)
plotshape(isExit, title="exit", style=shape.triangledown, location=location.abovebar, color=color.red, size=size.small)
"""
