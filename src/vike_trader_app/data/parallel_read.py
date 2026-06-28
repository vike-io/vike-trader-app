"""Parallel multi-symbol bar reads.

Safe to run off the main thread now that the read primitive uses per-call DuckDB connections
(see ``parquet_source.read_bars_parquet``). Qt-free — no PySide import.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)


def read_series_many(cat, symbols, interval, *, start=None, end=None, max_workers=None):
    """Read bars for many symbols CONCURRENTLY via ``cat.query``. Returns ``{symbol: list[Bar]}``.

    A symbol whose read raises maps to ``[]`` (logged) and never aborts the batch — a single
    corrupt/locked shard must not sink a screener rescan.
    """
    if not symbols:
        return {}
    workers = max_workers or min(8, (os.cpu_count() or 4))

    def _one(sym):
        try:
            return sym, cat.query(sym, interval, start, end)
        except Exception as e:  # noqa: BLE001 - isolate a bad/locked shard per symbol
            log.warning("read_series_many: %s/%s failed: %s", sym, interval, e)
            return sym, []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(_one, list(symbols)))
