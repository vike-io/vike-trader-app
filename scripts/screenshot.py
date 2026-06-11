"""Render the visual backtester offscreen to PNGs for verification.

Run:  uv run python scripts/screenshot.py
Uses the seeded Parquet if present, else fetches from Binance, else synthetic bars.
Produces shot_full.png (whole run) and shot_mid.png (replay paused mid-way) in the OS
temp dir under vike-shots/ — scratch screenshots never live in the repo.
"""

import math
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.dialogs import default_strategy_factory  # noqa: E402

SEED = "storage/parquet/BTCUSDT/1m.parquet"
OUT_DIR = os.path.join(tempfile.gettempdir(), "vike-shots")


def _load_bars():
    try:
        from vike_trader_app.data.parquet_source import read_bars_parquet

        bars = read_bars_parquet(SEED)
        if bars:
            print(f"loaded {len(bars)} bars from {SEED}")
            return bars
    except Exception as exc:  # noqa: BLE001
        print(f"parquet load skipped: {exc}")
    try:
        from vike_trader_app.data.binance_source import fetch_bars

        bars = fetch_bars("BTCUSDT", "1m", 500)
        print(f"fetched {len(bars)} bars from Binance")
        return bars
    except Exception as exc:  # noqa: BLE001
        print(f"binance fetch skipped: {exc}")
    bars = []
    price = 100.0
    for i in range(300):
        o = price
        price = 100 + 20 * math.sin(i / 15) + (i % 7) - 3
        c = price
        bars.append(
            Bar(ts=i * 60_000, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)
        )
    print(f"using {len(bars)} synthetic bars")
    return bars


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QtWidgets.QApplication([])
    win = MainWindow()
    win.resize(1360, 860)
    win.load_bars(_load_bars(), default_strategy_factory())
    win.show()
    for _ in range(5):
        app.processEvents()

    out_full = os.path.join(OUT_DIR, "shot_full.png")
    win.grab().save(out_full)
    print(f"wrote {out_full}")

    win._replay.seek(win._replay.last_index // 2)
    win._render_frame()
    for _ in range(5):
        app.processEvents()
    out_mid = os.path.join(OUT_DIR, "shot_mid.png")
    win.grab().save(out_mid)
    print(f"wrote {out_mid}")


if __name__ == "__main__":
    main()
