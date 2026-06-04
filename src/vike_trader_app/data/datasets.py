"""DataSets — named symbol collections (Wealth-Lab's first-class concept).

A DataSet bundles a list of symbols with an optional default provider + interval, so the Data
Manager can download/update a whole universe ("Crypto Majors", "My FX") in one action. Stored as
human-editable JSON under ``<root>/datasets/<slug>.json``, mirroring the broker-profile / pins
storage convention.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DateRange:
    """A membership window [start_ts, end_ts] in epoch ms. end_ts None = open-ended (still a member)."""

    start_ts: int
    end_ts: int | None = None

    def contains(self, ts: int) -> bool:
        return ts >= self.start_ts and (self.end_ts is None or ts <= self.end_ts)


@dataclass
class DataSet:
    """A named collection of symbols + how to fetch them by default."""

    name: str
    symbols: list[str] = field(default_factory=list)
    provider: str | None = None   # None = Auto (infer crypto/forex), else an explicit provider
    interval: str = "1m"
    ranges: dict[str, list[DateRange]] = field(default_factory=dict)
    benchmark: str = ""  # optional benchmark symbol (e.g. "SPY", "BTCUSDT"); "" = equal-weight default

    def is_dynamic(self) -> bool:
        """True when any symbol has explicit membership windows (WealthLab dynamic DataSet)."""
        return any(self.ranges.values())

    def active_at(self, symbol: str, ts: int) -> bool:
        """Whether ``symbol`` is a member at ``ts``. A symbol with no ranges is always active."""
        windows = self.ranges.get(symbol)
        if not windows:
            return True
        return any(w.contains(ts) for w in windows)


def parse_symbols(text: str) -> list[str]:
    """Split a free-text symbol blob (commas / whitespace / newlines) → upper, deduped, ordered."""
    out: list[str] = []
    for tok in re.split(r"[\s,;]+", text.strip()):
        s = tok.strip().upper()
        if s and s not in out:
            out.append(s)
    return out


def provider_group(d: "DataSet") -> str | None:
    """The tree node a DataSet belongs under: 'Binance' (crypto) or 'Dukascopy' (FX), or None.

    A linked provider decides directly (crypto providers -> Binance node, dukascopy/yahoo -> Dukascopy
    node). Unlinked sets are inferred from their first symbol; an empty unlinked set has no group.
    """
    from .sources import CRYPTO_PROVIDERS, is_forex_symbol

    if d.provider in CRYPTO_PROVIDERS:
        return "Binance"
    if d.provider in ("dukascopy", "yahoo"):
        return "Dukascopy"
    if not d.symbols:
        return None
    return "Dukascopy" if is_forex_symbol(d.symbols[0]) else "Binance"


def datasets_dir(root: str) -> Path:
    return Path(root) / "datasets"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "dataset"


def dataset_path(root: str, name: str) -> Path:
    return datasets_dir(root) / f"{_slug(name)}.json"


def _dataset_to_dict(d: DataSet) -> dict:
    return {
        "name": d.name,
        "symbols": list(d.symbols),
        "provider": d.provider,
        "interval": d.interval,
        "ranges": {
            sym: [{"start_ts": w.start_ts, "end_ts": w.end_ts} for w in windows]
            for sym, windows in d.ranges.items()
        },
        "benchmark": d.benchmark,
    }


def _dataset_from_dict(data: dict) -> DataSet:
    ranges = {
        sym: [DateRange(w["start_ts"], w.get("end_ts")) for w in windows]
        for sym, windows in (data.get("ranges") or {}).items()
    }
    return DataSet(
        name=data["name"],
        symbols=list(data.get("symbols", [])),
        provider=data.get("provider"),
        interval=data.get("interval", "1m"),
        ranges=ranges,
        benchmark=data.get("benchmark", ""),
    )


def save_dataset(d: DataSet, root: str) -> None:
    path = dataset_path(root, d.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_dataset_to_dict(d), indent=2), encoding="utf-8")


def load_dataset(name: str, root: str) -> DataSet | None:
    path = dataset_path(root, name)
    if not path.exists():
        return None
    return _dataset_from_dict(json.loads(path.read_text()))


def list_datasets(root: str) -> list[str]:
    d = datasets_dir(root)
    if not d.is_dir():
        return []
    return sorted(json.loads(f.read_text())["name"] for f in d.glob("*.json"))


def delete_dataset(name: str, root: str) -> None:
    dataset_path(root, name).unlink(missing_ok=True)


def preset_datasets() -> dict[str, DataSet]:
    """Built-in example DataSets so the panel isn't empty on first open."""
    return {
        "Crypto Majors": DataSet(
            "Crypto Majors",
            ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"],
            provider=None, interval="1m",
        ),
        "FX Majors": DataSet(
            "FX Majors",
            ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD"],
            provider="dukascopy", interval="1h",
        ),
    }


def ensure_examples(root: str) -> list[str]:
    """Write any example DataSet not already on disk; return names written (idempotent)."""
    written = []
    for name, d in preset_datasets().items():
        if not dataset_path(root, name).exists():
            save_dataset(d, root)
            written.append(name)
    return sorted(written)
