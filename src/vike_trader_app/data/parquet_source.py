"""Local Parquet store for bars, read/written with Polars."""

from pathlib import Path

import polars as pl

from ..core.model import Bar


def bars_to_dataframe(bars: list[Bar]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ts": [b.ts for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )


def dataframe_to_bars(df: pl.DataFrame) -> list[Bar]:
    return [
        Bar(
            ts=r["ts"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
        )
        for r in df.iter_rows(named=True)
    ]


def write_bars_parquet(bars: list[Bar], path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    bars_to_dataframe(bars).write_parquet(path)


def read_bars_parquet(path) -> list[Bar]:
    return dataframe_to_bars(pl.read_parquet(path))
