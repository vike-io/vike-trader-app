"""DuckDB-backed read-only catalog over the local Parquet cache — Phase-0 spike.

A drop-in for :class:`~vike_trader_app.data.catalog.Catalog` that reads the *same*
``<root>/<symbol>/<interval>.parquet`` files through DuckDB instead of loading every bar
into Python. The win this validates (design doc §3 G1/G7/G10): ``info()`` answers
count/min/max from Parquet statistics rather than materialising the whole series, and a
DuckDB connection is cheap to create per thread — retiring the main-thread-only constraint
the Polars/mmap reader imposes.

DuckDB is an optional dependency (``[duck]`` extra); it's imported lazily so importing this
module never forces the dependency — only instantiating ``DuckCatalog`` does. Filesystem
discovery (symbols/intervals) stays pure-pathlib, identical to ``Catalog``.
"""

from pathlib import Path

from .catalog import DatasetInfo
from .cache import DEFAULT_ROOT

# Columns written by parquet_source.write_bars_parquet (no `funding` — matches the Polars path).
_COLS = "ts, open, high, low, close, volume"
_I64_MIN = -(2**63)
_I64_MAX = 2**63 - 1


class DuckCatalog:
    """Discover and query the local Parquet datasets under ``root`` via DuckDB."""

    def __init__(self, root: str = DEFAULT_ROOT):
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "DuckCatalog needs the optional 'duck' extra: pip install 'vike-trader-app[duck]'"
            ) from exc
        self.root = Path(root)
        # One in-memory connection per instance. For multi-threaded use, give each thread its
        # own connection (or `self._con.cursor()`) — DuckDB connections aren't shared across threads.
        self._con = duckdb.connect(database=":memory:")

    def symbols(self) -> list[str]:
        """Symbols that have at least one cached interval, sorted (legacy or partitioned)."""
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and any(p.glob("**/*.parquet")))

    def intervals(self, symbol: str) -> list[str]:
        """Cached intervals for ``symbol``, sorted — legacy file stems + partition dirs."""
        d = self.root / symbol
        if not d.is_dir():
            return []
        out = {p.stem for p in d.glob("*.parquet")}
        out |= {sub.name for sub in d.iterdir() if sub.is_dir() and any(sub.glob("*.parquet"))}
        return sorted(out)

    def _files(self, symbol: str, interval: str) -> list[str]:
        """All Parquet files backing ``(symbol, interval)`` — legacy single file + month partitions."""
        files: list[str] = []
        legacy = self.root / symbol / f"{interval}.parquet"
        if legacy.exists():
            files.append(legacy.as_posix())
        d = self.root / symbol / interval
        if d.is_dir():
            files += [p.as_posix() for p in sorted(d.glob("*.parquet"))]
        return files

    def _src(self, symbol: str, interval: str) -> str | None:
        """A DuckDB ``read_parquet([...])`` source over the series' files, or None if none exist.

        Paths come from controlled symbol/interval/month names (no quotes), so inlining the file
        list as a SQL literal is safe — and lets DuckDB prune partitions/row-groups across them.
        """
        files = self._files(symbol, interval)
        if not files:
            return None
        return "read_parquet([" + ", ".join("'" + f + "'" for f in files) + "])"

    def info(self, symbol: str, interval: str) -> DatasetInfo | None:
        """Metadata for one dataset, or None if it isn't cached / is empty.

        Answers count + min/max ts straight from Parquet statistics via DuckDB (across all the
        series' partitions), without reading the full series into Python — the O(1)-ish win.
        """
        src = self._src(symbol, interval)
        if src is None:
            return None
        n, lo, hi = self._con.execute(f"SELECT count(*), min(ts), max(ts) FROM {src}").fetchone()
        if not n:
            return None
        return DatasetInfo(symbol, interval, int(n), int(lo), int(hi),
                           str(self.root / symbol / interval))

    def list_datasets(self) -> list[DatasetInfo]:
        """Every cached dataset's metadata, sorted by (symbol, interval)."""
        out: list[DatasetInfo] = []
        for symbol in self.symbols():
            for interval in self.intervals(symbol):
                ds = self.info(symbol, interval)
                if ds is not None:
                    out.append(ds)
        return out

    def query(self, symbol: str, interval: str, start: int | None = None, end: int | None = None):
        """Bars for ``symbol``/``interval`` in ``[start, end]`` (inclusive); ``[]`` if absent."""
        from ..core.model import Bar

        src = self._src(symbol, interval)
        if src is None:
            return []
        lo = start if start is not None else _I64_MIN
        hi = end if end is not None else _I64_MAX
        rows = self._con.execute(
            f"SELECT {_COLS} FROM {src} WHERE ts BETWEEN ? AND ? ORDER BY ts",
            [lo, hi],
        ).fetchall()
        return [Bar(ts=int(r[0]), open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
                for r in rows]

    def get_or_derive(self, symbol: str, interval: str, start: int | None = None,
                      end: int | None = None, base: str = "1m"):
        """Serve ``interval`` from its own Parquet if cached, else derive it from the ``base``.

        The "one base, derive the rest" read path (Phase 2 foundation): a timeframe the source
        provides natively is served as-is; anything else is resampled from the stored base via
        DuckDB, so we don't need a separate cached file per timeframe. ``[]`` if neither exists.
        """
        if interval == base:
            return self.query(symbol, base, start, end)
        if self._files(symbol, interval):
            return self.query(symbol, interval, start, end)
        if self._files(symbol, base):
            from ..core.timeframe import parse_timeframe

            return self.resample(symbol, base, parse_timeframe(interval), start, end)
        return []

    def resample(self, symbol: str, base_interval: str, target_ms: int,
                 start: int | None = None, end: int | None = None):
        """Aggregate the ``base_interval`` series into ``target_ms`` buckets straight from Parquet.

        Derives any timeframe from one stored base (the "1m base, derive the rest" pattern)
        without materialising the base series into Python. Epoch-aligned and **byte-identical to
        ``core.timeframe.resample``**: open=first, high=max, low=min, close=last, volume=sum, and
        the final (possibly partial) bucket is included. ``[]`` if the base isn't cached.
        """
        from ..core.model import Bar

        src = self._src(symbol, base_interval)
        if src is None:
            return []
        lo = start if start is not None else _I64_MIN
        hi = end if end is not None else _I64_MAX
        # Filter on the BUCKET, not the base ts: a WHERE ts BETWEEN pre-filter truncates the base
        # bars feeding the edge buckets (a mid-bucket start/end produced a partial/phantom candle).
        # HAVING keeps only whole buckets whose start falls in [lo, hi]; each is aggregated from ALL
        # its base bars, so edge buckets are correct.
        rows = self._con.execute(
            f"SELECT (ts - ts % ?) AS bucket, arg_min(open, ts), max(high), min(low), "
            f"arg_max(close, ts), sum(volume) FROM {src} "
            f"GROUP BY bucket HAVING bucket BETWEEN ? AND ? ORDER BY bucket",
            [target_ms, lo, hi],
        ).fetchall()
        return [Bar(ts=int(r[0]), open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
                for r in rows]
