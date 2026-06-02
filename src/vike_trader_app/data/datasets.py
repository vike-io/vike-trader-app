"""DataSets — named symbol collections (Wealth-Lab's first-class concept).

A DataSet bundles a list of symbols with an optional default provider + interval, so the Data
Manager can download/update a whole universe ("Crypto Majors", "My FX") in one action. Stored as
human-editable JSON under ``<root>/datasets/<slug>.json``, mirroring the broker-profile / pins
storage convention.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class DataSet:
    """A named collection of symbols + how to fetch them by default."""

    name: str
    symbols: list[str] = field(default_factory=list)
    provider: str | None = None   # None = Auto (infer crypto/forex), else an explicit provider
    interval: str = "1m"


def parse_symbols(text: str) -> list[str]:
    """Split a free-text symbol blob (commas / whitespace / newlines) → upper, deduped, ordered."""
    out: list[str] = []
    for tok in re.split(r"[\s,;]+", text.strip()):
        s = tok.strip().upper()
        if s and s not in out:
            out.append(s)
    return out


def datasets_dir(root: str) -> Path:
    return Path(root) / "datasets"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "dataset"


def dataset_path(root: str, name: str) -> Path:
    return datasets_dir(root) / f"{_slug(name)}.json"


def save_dataset(d: DataSet, root: str) -> None:
    path = dataset_path(root, d.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(d), indent=2))


def load_dataset(name: str, root: str) -> DataSet | None:
    path = dataset_path(root, name)
    if not path.exists():
        return None
    return DataSet(**json.loads(path.read_text()))


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
    }


def ensure_examples(root: str) -> list[str]:
    """Write any example DataSet not already on disk; return names written (idempotent)."""
    written = []
    for name, d in preset_datasets().items():
        if not dataset_path(root, name).exists():
            save_dataset(d, root)
            written.append(name)
    return sorted(written)
