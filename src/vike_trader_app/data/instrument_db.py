"""SQLite-backed instrument store — broker profiles + per-exchange symbol catalog.

Why a database: per the project rule, **runtime state lives in the app's SQLite store**
(``storage/db/``, beside ``vike_trader_app.sqlite``), never in loose JSON files. JSON survives
only as the explicit, user-triggered Export/Import interchange (see
:func:`.instruments.export_profile_json` / :func:`.instruments.import_profile_json`). The legacy
``<root>/profiles/*.json`` store is swept into the DB once by :func:`migrate_json_profiles`
(user edits preserved, files deleted) — after that nothing reads or writes those files in
normal operation.

Schema choice — a profile keeps its per-symbol overrides as ONE JSON-encoded column rather than
a normalized rows table: a profile is only ever loaded/saved *whole* (the editor rebuilds it on
save), so a single-row read/write is atomic by construction, and the codec is the exact dict
form already used for export/import interchange (``profile_to_dict``/``profile_from_dict``) —
one format, no drift. The **symbol catalog** IS normalized on ``(exchange, symbol)`` because it
is queried per symbol at resolve time and refreshed in ~2k-row batches per exchange
(Binance ``/exchangeInfo``).

Threading: connections are opened and closed per call **on the caller's thread** — the data
layer is not thread-safe by repo convention (see CLAUDE.md); nothing here spawns threads.
Uses stdlib sqlite3 (no extra dependency — mirrors :mod:`.store`).
"""

import json
import logging
import sqlite3
import time
import urllib.request
from contextlib import closing
from pathlib import Path

from .instruments import (
    ASSET_CRYPTO,
    BrokerProfile,
    InstrumentSpec,
    profile_from_dict,
    profile_to_dict,
    profiles_dir,
)

log = logging.getLogger(__name__)

DB_FILENAME = "instruments.sqlite"

#: Public Binance spot endpoint the default catalog refresh hits (no API key needed).
BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
#: Exchange key the Binance catalog is stored under — the slug of the "Binance" profile name,
#: so :func:`.instruments.resolve_spec` finds it via the profile that names the exchange.
BINANCE_EXCHANGE = "binance"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    name         TEXT PRIMARY KEY,
    timezone     TEXT NOT NULL DEFAULT 'UTC',
    asset_class  TEXT NOT NULL DEFAULT 'crypto',
    postfix      TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    instruments  TEXT NOT NULL DEFAULT '{}',  -- JSON {SYMBOL: spec-dict}: whole-profile I/O
    default_spec TEXT                          -- JSON spec-dict, or NULL
);

CREATE TABLE IF NOT EXISTS symbol_catalog (
    exchange       TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    asset_class    TEXT NOT NULL DEFAULT 'crypto',
    tick_size      REAL NOT NULL,
    pip_size       REAL NOT NULL,
    volume_step    REAL NOT NULL DEFAULT 0,
    contract_size  REAL NOT NULL DEFAULT 1,
    quote_ccy      TEXT NOT NULL DEFAULT '',
    base_ccy       TEXT NOT NULL DEFAULT '',
    price_decimals INTEGER,                    -- NULL -> derived from tick (InstrumentSpec.decimals)
    updated_ts     INTEGER NOT NULL,
    PRIMARY KEY (exchange, symbol)
);
"""

_PROFILE_COLS = "name, timezone, asset_class, postfix, description, instruments, default_spec"
_CATALOG_COLS = ("exchange, symbol, asset_class, tick_size, pip_size, volume_step, "
                 "contract_size, quote_ccy, base_ccy, price_decimals, updated_ts")

# Roots whose legacy JSON store has been swept this process. The sweep itself is idempotent —
# the memo just keeps hot paths (e.g. per-row spec lookups in the Data Manager) from re-statting
# the legacy directory on every call.
_MIGRATED: set[str] = set()


def db_path(root: str) -> Path:
    """The instrument DB file for a config ``root`` — ``<root>/db/instruments.sqlite``.

    Same directory as the existing app DB (``vike_trader_app.sqlite``) so all SQLite state
    lives in one place; a separate file keeps instrument metadata independently portable.
    """
    return Path(root) / "db" / DB_FILENAME


def connect(root: str) -> sqlite3.Connection:
    """Open the DB (creating dir + schema). Schema-only: never triggers the JSON migration."""
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def open_db(root: str) -> sqlite3.Connection:
    """The store entry point: open the DB, lazily sweeping the legacy JSON store first.

    Every public read/write goes through here, so the one-time migration is guaranteed to run
    before anything else can observe (or shadow) the profiles table. The memo is added only
    after a successful sweep so a transient failure is retried on the next call.
    """
    key = str(Path(root).resolve())
    if key not in _MIGRATED:
        migrate_json_profiles(root)
        _MIGRATED.add(key)
    return connect(root)


# --- one-time legacy JSON migration ---------------------------------------------------------

def migrate_json_profiles(root: str) -> list[str]:
    """Sweep the legacy ``<root>/profiles/*.json`` store into the DB. Returns imported names.

    Idempotent, with deliberately asymmetric semantics:

    * a file is imported **only if no profile with that name is stored yet** — the DB is the
      source of truth from the moment it has a row, so re-running after a partial failure can
      never clobber edits made in the app since;
    * every successfully *handled* file is **deleted** (imported, or already superseded by a
      DB row) — after migration nothing reads or writes the legacy store. Bringing a JSON back
      in later is the explicit Import action, not a file drop;
    * a file that fails to parse is left in place (and logged) so the user can recover it by
      hand or via :func:`.instruments.import_profile_json`.
    """
    d = profiles_dir(root)
    if not d.is_dir():
        return []
    imported: list[str] = []
    with closing(connect(root)) as conn:
        for f in sorted(d.glob("*.json")):
            try:
                profile = profile_from_dict(json.loads(f.read_text()))
            except (json.JSONDecodeError, KeyError, TypeError, OSError) as exc:
                log.warning("instrument-db migration: leaving unreadable %s in place (%s)", f, exc)
                continue
            with conn:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO profiles ({_PROFILE_COLS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    _profile_params(profile),
                )
            if cur.rowcount:
                imported.append(profile.name)
            try:
                f.unlink()  # imported or superseded either way — the DB is truth now
            except OSError as exc:
                log.warning("instrument-db migration: could not delete %s (%s)", f, exc)
    try:  # leave no empty legacy dir behind (best-effort; non-empty means skipped files stay)
        d.rmdir()
    except OSError:
        pass
    if imported:
        log.info("instrument-db migration: imported %d profile(s) from legacy JSON: %s",
                 len(imported), ", ".join(sorted(imported)))
    return sorted(imported)


# --- profiles --------------------------------------------------------------------------------

def _profile_params(p: BrokerProfile) -> tuple:
    d = profile_to_dict(p)
    return (d["name"], d["timezone"], d["asset_class"], d["postfix"], d["description"],
            json.dumps(d["instruments"]),
            json.dumps(d["default_spec"]) if d["default_spec"] else None)


def _row_to_profile(row: sqlite3.Row) -> BrokerProfile:
    return profile_from_dict({
        "name": row["name"], "timezone": row["timezone"], "asset_class": row["asset_class"],
        "postfix": row["postfix"], "description": row["description"],
        "instruments": json.loads(row["instruments"]),
        "default_spec": json.loads(row["default_spec"]) if row["default_spec"] else None,
    })


def put_profile(root: str, profile: BrokerProfile) -> None:
    """Insert or replace ``profile`` (keyed by name)."""
    with closing(open_db(root)) as conn, conn:
        conn.execute(
            f"INSERT OR REPLACE INTO profiles ({_PROFILE_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            _profile_params(profile),
        )


def add_profile_if_absent(root: str, profile: BrokerProfile) -> bool:
    """Insert ``profile`` only if its name isn't stored yet; True if inserted.

    The preset-seeding primitive: never clobbers a row, so user edits to a preset survive.
    """
    with closing(open_db(root)) as conn, conn:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO profiles ({_PROFILE_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            _profile_params(profile),
        )
    return bool(cur.rowcount)


def get_profile(root: str, name: str) -> BrokerProfile | None:
    with closing(open_db(root)) as conn:
        row = conn.execute("SELECT * FROM profiles WHERE name = ?", (name,)).fetchone()
    return _row_to_profile(row) if row else None


def profile_names(root: str) -> list[str]:
    with closing(open_db(root)) as conn:
        rows = conn.execute("SELECT name FROM profiles ORDER BY name").fetchall()
    return [r["name"] for r in rows]


# --- symbol catalog --------------------------------------------------------------------------

def catalog_upsert(root: str, exchange: str, specs: list[InstrumentSpec]) -> int:
    """Insert-or-replace ``specs`` under ``exchange`` (case-folded); returns the row count.

    One transaction for the whole batch so a ~2k-symbol refresh is atomic: a reader never sees
    a half-updated exchange.
    """
    now = int(time.time())
    rows = [(exchange.lower(), s.symbol.upper(), s.asset_class, s.tick_size, s.pip_size,
             s.volume_step, s.contract_size, s.quote_ccy, s.base_ccy, s.price_decimals, now)
            for s in specs]
    with closing(open_db(root)) as conn, conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO symbol_catalog ({_CATALOG_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def catalog_lookup(root: str, exchange: str, symbol: str) -> InstrumentSpec | None:
    """The spec stored for ``(exchange, symbol)``, or None — the first resolution layer."""
    with closing(open_db(root)) as conn:
        row = conn.execute(
            "SELECT * FROM symbol_catalog WHERE exchange = ? AND symbol = ?",
            (exchange.lower(), symbol.upper()),
        ).fetchone()
    if row is None:
        return None
    return InstrumentSpec(
        symbol=row["symbol"], asset_class=row["asset_class"], tick_size=row["tick_size"],
        pip_size=row["pip_size"], volume_step=row["volume_step"],
        contract_size=row["contract_size"], quote_ccy=row["quote_ccy"],
        base_ccy=row["base_ccy"], price_decimals=row["price_decimals"],
    )


# --- Binance /exchangeInfo refresh -----------------------------------------------------------

def parse_exchange_info(payload: dict) -> list[InstrumentSpec]:
    """Pure parse of a Binance ``/exchangeInfo`` payload into specs (the testable half).

    Per symbol: ``PRICE_FILTER.tickSize`` -> tick (pip == tick, the repo's crypto convention),
    ``LOT_SIZE.stepSize`` -> volume step, ``baseAsset``/``quoteAsset`` -> currencies. Entries
    without a positive tick are skipped — a spec that can't price is worse than the fallback.
    """
    specs: list[InstrumentSpec] = []
    for entry in payload.get("symbols", []):
        filters = {f.get("filterType"): f for f in entry.get("filters", [])}
        try:
            tick = float(filters.get("PRICE_FILTER", {}).get("tickSize", 0))
            step = float(filters.get("LOT_SIZE", {}).get("stepSize", 0))
        except (TypeError, ValueError):
            continue
        if tick <= 0:
            continue
        specs.append(InstrumentSpec(
            symbol=str(entry.get("symbol", "")).upper(), asset_class=ASSET_CRYPTO,
            tick_size=tick, pip_size=tick, volume_step=step, contract_size=1.0,
            quote_ccy=str(entry.get("quoteAsset", "")), base_ccy=str(entry.get("baseAsset", "")),
        ))
    return [s for s in specs if s.symbol]


def parse_symbol_filters(payload: dict) -> dict[str, dict]:
    """Per-symbol order-placement bounds from /exchangeInfo (for RiskLimits, not InstrumentSpec).

    Keeps PRICE_FILTER.tickSize, LOT_SIZE.{stepSize,minQty,maxQty}, and NOTIONAL/MIN_NOTIONAL.
    minNotional — filters parse_exchange_info drops. Absent filters default to 0.0.
    """
    out: dict[str, dict] = {}
    for entry in payload.get("symbols", []):
        filters = {f.get("filterType"): f for f in entry.get("filters", [])}

        def _f(ftype: str, field: str) -> float:
            try:
                return float(filters.get(ftype, {}).get(field, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        try:
            min_notional = float(notional.get("minNotional", 0) or 0)
        except (TypeError, ValueError):
            min_notional = 0.0
        symbol = str(entry.get("symbol", "")).upper()
        if not symbol:
            continue
        out[symbol] = {
            "tick_size": _f("PRICE_FILTER", "tickSize"),
            "step_size": _f("LOT_SIZE", "stepSize"),
            "min_qty": _f("LOT_SIZE", "minQty"),
            "max_qty": _f("LOT_SIZE", "maxQty"),
            "min_notional": min_notional,
        }
    return out


def _default_fetch() -> dict:
    """GET + parse the live exchangeInfo (stdlib urllib, matching :mod:`.binance_source`)."""
    req = urllib.request.Request(BINANCE_EXCHANGE_INFO_URL,
                                 headers={"User-Agent": "vike-trader-app"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https host
        return json.loads(resp.read().decode("utf-8"))


def refresh_binance_catalog(root: str, fetch=None) -> int:
    """Refresh the Binance symbol catalog; returns how many symbols were upserted.

    ``fetch`` is the injectable seam: a no-arg callable returning the parsed ``/exchangeInfo``
    dict. Tests pass a canned payload — only the default (None) ever touches the network, and
    it runs on the caller's thread like every other data-layer fetch.
    """
    payload = (fetch or _default_fetch)()
    specs = parse_exchange_info(payload)
    n = catalog_upsert(root, BINANCE_EXCHANGE, specs)
    log.info("symbol catalog: upserted %d %s symbols", n, BINANCE_EXCHANGE)
    return n
