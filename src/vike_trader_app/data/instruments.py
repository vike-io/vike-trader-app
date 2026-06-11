"""Instrument metadata + broker profiles — the Data-Manager keystone.

What it buys us: a downloaded series stops being an anonymous OHLC table and becomes
*self-describing*. An :class:`InstrumentSpec` carries the tick / pip / volume-step /
contract-size, which is what you need to

* format prices with the right number of decimals (``decimals`` is derived from the tick),
* resample / round consistently,
* map a broker-tagged symbol back to its instrument,
* (later) export to CSV / MT4 / MT5.

A :class:`BrokerProfile` is a named bundle of specs plus a *display-only* ``timezone`` label.
Per the project decision, **all bars are stored and queried in UTC** — the timezone is metadata
you can read (e.g. "Dukascopy = EST+7"), never a second physical copy of the data.

Storage is the SQLite instrument DB (see :mod:`.instrument_db`): profiles live in
``<root>/db/instruments.sqlite`` beside the app DB — per the project rule, runtime state lives
in SQLite, never in loose JSON files. The legacy ``<root>/profiles/<slug>.json`` store is
migrated in once on first open (user edits preserved, files deleted). JSON remains only as the
explicit Export/Import interchange (:func:`export_profile_json` / :func:`import_profile_json`).

Resolution consults three layers, in order: the per-exchange ``symbol_catalog`` (refreshed from
the venue, e.g. Binance ``/exchangeInfo``) → the profile's manual per-symbol override → the
asset-class default (the original fallback, preserved).
"""

import json
import re
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Asset classes we tag specs with (kept as plain strings for trivial JSON round-tripping).
ASSET_CRYPTO = "crypto"
ASSET_STOCK = "stock"
ASSET_FOREX = "forex"
ASSET_CFD = "cfd"


def decimals_for_tick(tick_size: float) -> int:
    """Number of price decimals implied by a tick — ``0.01 -> 2``, ``0.00001 -> 5``, ``1 -> 0``.

    Uses :class:`~decimal.Decimal` on the *string* form so float noise (``0.00001`` printing as
    ``1e-05``) doesn't inflate the count. Non-positive ticks (unknown) format as whole numbers.
    """
    if tick_size <= 0:
        return 0
    try:
        exp = Decimal(str(tick_size)).normalize().as_tuple().exponent
    except InvalidOperation:
        return 0
    return max(0, -exp) if isinstance(exp, int) else 0


@dataclass(frozen=True)
class InstrumentSpec:
    """The tradable characteristics of one symbol — enough to describe + price its data."""

    symbol: str
    asset_class: str = ASSET_CRYPTO
    tick_size: float = 0.01           # minimum price increment
    pip_size: float = 0.01            # one pip (FX: 0.0001); non-FX defaults to the tick
    volume_step: float = 0.0          # minimum order-size increment ("step"); 0 = unset
    contract_size: float = 1.0        # units per 1 lot (FX standard lot = 100_000)
    quote_ccy: str = ""               # e.g. "USD" / "USDT"
    base_ccy: str = ""                # e.g. "BTC" / "EUR"
    price_decimals: int | None = None  # explicit override; None -> derived from tick_size

    @property
    def decimals(self) -> int:
        """Price decimals to display — the override if set, else derived from ``tick_size``."""
        return self.price_decimals if self.price_decimals is not None else decimals_for_tick(self.tick_size)

    def format_price(self, value: float) -> str:
        """``value`` rounded to this instrument's decimals, e.g. BTC ``12345.68``."""
        return f"{value:.{self.decimals}f}"


def default_spec_for(symbol: str, asset_class: str) -> InstrumentSpec:
    """A sensible fallback spec for a symbol with no explicit entry, by asset class."""
    if asset_class == ASSET_STOCK:
        return InstrumentSpec(symbol, ASSET_STOCK, tick_size=0.01, pip_size=0.01,
                              volume_step=1, contract_size=1.0, quote_ccy="USD")
    if asset_class in (ASSET_FOREX, ASSET_CFD):
        return InstrumentSpec(symbol, asset_class, tick_size=0.00001, pip_size=0.0001,
                              volume_step=0.01, contract_size=100_000.0)
    # crypto (default)
    return InstrumentSpec(symbol, ASSET_CRYPTO, tick_size=0.01, pip_size=0.01,
                          volume_step=0.00001, contract_size=1.0)


def strip_postfix(symbol: str, postfix: str) -> str:
    """Drop a broker ``postfix`` (and surrounding case) so a tagged symbol maps to its spec."""
    s = symbol.strip()
    if postfix and s.lower().endswith(postfix.lower()):
        s = s[: -len(postfix)]
    return s.upper()


@dataclass(frozen=True)
class BrokerProfile:
    """A named bundle of instrument specs + a display-only timezone label.

    ``resolve`` maps a (possibly postfix-tagged) symbol to its spec, falling back to
    ``default_spec`` or an asset-class default so every symbol gets *some* spec.
    """

    name: str
    timezone: str = "UTC"             # label only — data is always stored/queried in UTC
    asset_class: str = ASSET_CRYPTO
    postfix: str = ""                 # appended to symbols to mark this broker as the source
    description: str = ""
    instruments: dict[str, InstrumentSpec] = field(default_factory=dict)
    default_spec: InstrumentSpec | None = None

    def resolve(self, symbol: str, catalog=None) -> InstrumentSpec:
        """Spec for ``symbol``: catalog hit → manual override → class default.

        ``catalog`` is an optional injectable lookup (postfix-stripped symbol →
        ``InstrumentSpec | None``) bound to this broker's exchange row in the symbol catalog —
        see :func:`resolve_spec`. The catalog outranks the profile's manual entries by design:
        a venue-refreshed spec is authoritative, while the manual table covers symbols the
        venue doesn't publish. Kept as a parameter (not a field) so the dataclass stays a pure,
        equality-comparable value with no storage coupling.
        """
        key = strip_postfix(symbol, self.postfix)
        if catalog is not None:
            hit = catalog(key)
            if hit is not None:
                return hit
        if key in self.instruments:
            return self.instruments[key]
        if self.default_spec is not None:
            return replace(self.default_spec, symbol=key)
        return default_spec_for(key, self.asset_class)


# --- presets (approved scope: Binance / Bybit / Coinbase / US Equities / Generic) ----------

# A small known-tick table for crypto majors; everything else uses the crypto default.
_CRYPTO_TICKS = {
    "BTCUSDT": (0.01, 0.00001), "ETHUSDT": (0.01, 0.0001), "SOLUSDT": (0.01, 0.001),
    "BNBUSDT": (0.01, 0.001), "XRPUSDT": (0.0001, 0.1), "ADAUSDT": (0.0001, 0.1),
    "DOGEUSDT": (0.00001, 1.0), "LTCUSDT": (0.01, 0.001),
}


def _crypto_instruments() -> dict[str, InstrumentSpec]:
    out: dict[str, InstrumentSpec] = {}
    for sym, (tick, step) in _CRYPTO_TICKS.items():
        base, quote = sym[:-4], sym[-4:]  # ...USDT
        out[sym] = InstrumentSpec(sym, ASSET_CRYPTO, tick_size=tick, pip_size=tick,
                                  volume_step=step, contract_size=1.0,
                                  quote_ccy=quote, base_ccy=base)
    return out


def _crypto_profile(name: str, postfix: str, description: str) -> BrokerProfile:
    return BrokerProfile(
        name=name, timezone="UTC", asset_class=ASSET_CRYPTO, postfix=postfix,
        description=description, instruments=_crypto_instruments(),
        default_spec=default_spec_for("", ASSET_CRYPTO),
    )


def preset_profiles() -> dict[str, BrokerProfile]:
    """The built-in profiles (the approved set): 3 crypto exchanges, US equities, a UTC default."""
    return {
        "Binance": _crypto_profile("Binance", "", "Binance crypto spot — quotes in USDT, UTC."),
        "Bybit": _crypto_profile("Bybit", ".bybit", "Bybit crypto — quotes in USDT, UTC."),
        "Coinbase": _crypto_profile("Coinbase", ".cb", "Coinbase crypto — quotes in USD, UTC."),
        "US Equities": BrokerProfile(
            name="US Equities", timezone="America/New_York", asset_class=ASSET_STOCK,
            description="NYSE / Nasdaq stocks & ETFs — cent tick, whole-share step. TZ label only.",
            default_spec=default_spec_for("", ASSET_STOCK),
        ),
        "Generic": BrokerProfile(
            name="Generic", timezone="UTC", asset_class=ASSET_CRYPTO,
            description="Default fallback for any unmapped symbol — UTC, cent tick.",
            default_spec=default_spec_for("", ASSET_CRYPTO),
        ),
    }


# --- storage (SQLite via .instrument_db; JSON only as export/import interchange) -----------
#
# The .instrument_db import stays *inside* the functions: instrument_db imports the model +
# codecs from this module at top level, so the storage seam loads lazily to avoid the cycle
# (same pattern as infer_asset_class's local .sources import).

def profiles_dir(root: str) -> Path:
    """Where the *legacy* JSON profile store lived — read only by the one-time migration."""
    return Path(root) / "profiles"


def _slug(name: str) -> str:
    """Lower-kebab key for a profile name — doubles as its exchange key in the symbol catalog
    (e.g. ``"Binance"`` → ``"binance"``, the row :func:`~.instrument_db.refresh_binance_catalog`
    writes)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profile"


def profile_path(root: str, name: str) -> Path:
    """Where the legacy JSON for ``name`` lived (kept for the migration sweep + tooling)."""
    return profiles_dir(root) / f"{_slug(name)}.json"


def _spec_to_dict(s: InstrumentSpec) -> dict:
    return asdict(s)


def _spec_from_dict(d: dict) -> InstrumentSpec:
    return InstrumentSpec(**d)


def profile_to_dict(p: BrokerProfile) -> dict:
    return {
        "name": p.name, "timezone": p.timezone, "asset_class": p.asset_class,
        "postfix": p.postfix, "description": p.description,
        "instruments": {k: _spec_to_dict(v) for k, v in p.instruments.items()},
        "default_spec": _spec_to_dict(p.default_spec) if p.default_spec else None,
    }


def profile_from_dict(d: dict) -> BrokerProfile:
    return BrokerProfile(
        name=d["name"], timezone=d.get("timezone", "UTC"),
        asset_class=d.get("asset_class", ASSET_CRYPTO), postfix=d.get("postfix", ""),
        description=d.get("description", ""),
        instruments={k: _spec_from_dict(v) for k, v in d.get("instruments", {}).items()},
        default_spec=_spec_from_dict(d["default_spec"]) if d.get("default_spec") else None,
    )


def save_profile(profile: BrokerProfile, root: str) -> None:
    """Insert-or-replace one profile in ``<root>/db/instruments.sqlite`` (keyed by name)."""
    from . import instrument_db

    instrument_db.put_profile(root, profile)


def load_profile(name: str, root: str) -> BrokerProfile | None:
    """Load a profile by name from the instrument DB, or ``None`` if not stored."""
    from . import instrument_db

    return instrument_db.get_profile(root, name)


def list_profiles(root: str) -> list[str]:
    """Names of all stored profiles (sorted), ``[]`` if none."""
    from . import instrument_db

    return instrument_db.profile_names(root)


def ensure_presets(root: str) -> list[str]:
    """Store any preset profile that isn't already in the DB; return the names written (sorted).

    Idempotent: existing rows are never clobbered, so user edits to a preset survive. (And the
    DB open behind this sweeps the legacy JSON store first, so a migrated user-edited preset is
    already present — see :func:`~.instrument_db.migrate_json_profiles`.)
    """
    from . import instrument_db

    written = [name for name, profile in preset_profiles().items()
               if instrument_db.add_profile_if_absent(root, profile)]
    return sorted(written)


def export_profile_json(name: str, root: str, dest: str | Path) -> Path:
    """Write profile ``name`` to ``dest`` as interchange JSON; returns the path written.

    This (with :func:`import_profile_json`) is the *only* sanctioned JSON I/O for profiles —
    an explicit, user-triggered export for sharing/backup. Runtime state stays in the DB.
    """
    profile = load_profile(name, root)
    if profile is None:
        raise ValueError(f"unknown profile: {name!r}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(profile_to_dict(profile), indent=2))
    return dest


def import_profile_json(src: str | Path, root: str) -> BrokerProfile:
    """Read an interchange JSON and store it (replacing any same-name profile); returns it.

    The source file is the user's — it is read, never moved or deleted (unlike the one-time
    legacy migration, which owns and removes the files it sweeps).
    """
    profile = profile_from_dict(json.loads(Path(src).read_text()))
    save_profile(profile, root)
    return profile


def with_instrument(profile: BrokerProfile, spec: InstrumentSpec) -> BrokerProfile:
    """A copy of ``profile`` with ``spec`` added (or replacing the existing entry for its symbol)."""
    instruments = dict(profile.instruments)
    instruments[spec.symbol.upper()] = spec
    return replace(profile, instruments=instruments)


def without_instrument(profile: BrokerProfile, symbol: str) -> BrokerProfile:
    """A copy of ``profile`` with ``symbol`` removed (no-op if absent)."""
    instruments = {k: v for k, v in profile.instruments.items() if k != symbol.upper()}
    return replace(profile, instruments=instruments)


# Numeric/text fields the editor (and bulk edit) may change on a spec.
EDITABLE_FIELDS = ("asset_class", "tick_size", "pip_size", "volume_step", "contract_size",
                   "price_decimals")


def mass_edit_specs(specs: list[InstrumentSpec], changes: dict) -> list[InstrumentSpec]:
    """Apply ``changes`` (a subset of :data:`EDITABLE_FIELDS`) to every spec — QDM 'mass edit'.

    Returns new frozen specs; ignores keys outside :data:`EDITABLE_FIELDS`.
    """
    safe = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    return [replace(s, **safe) for s in specs]


def resolve_spec(symbol: str, profile_name: str, root: str) -> InstrumentSpec:
    """Resolve ``symbol`` to its spec under the stored profile ``profile_name``.

    Resolution order: the profile's exchange row in the ``symbol_catalog`` (keyed by the
    profile's slug, e.g. ``Binance`` → ``binance``) → the profile's manual override → the
    asset-class default (the original behavior, preserved as the final fallback). Falls back
    to the in-memory preset (then a class default) if the profile isn't stored yet.
    """
    from . import instrument_db

    profile = load_profile(profile_name, root) or preset_profiles().get(profile_name)
    if profile is None:
        return default_spec_for(strip_postfix(symbol, ""), ASSET_CRYPTO)
    exchange = _slug(profile.name)
    return profile.resolve(
        symbol, catalog=lambda sym: instrument_db.catalog_lookup(root, exchange, sym)
    )


# --- symbol → asset class / spec (used to make a cached series self-describing) -------------

# Common crypto quote tokens; a pair ending in one of these is treated as crypto.
CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI")


def infer_asset_class(symbol: str) -> str:
    """Best-effort asset class from the ticker alone: forex pair → stock → default crypto.

    A 6-letter pair of currency codes (``EURUSD``) is forex; a ``…USDT``/``…USD`` pair is crypto;
    a short all-alpha ticker (``AAPL``) is a stock; anything else defaults to crypto.
    """
    from .sources import is_forex_symbol  # local import: avoids any import-time coupling

    s = symbol.upper()
    if is_forex_symbol(s):
        return ASSET_FOREX
    if s.endswith(CRYPTO_QUOTES) or s.endswith("USD"):
        return ASSET_CRYPTO
    if s.isalpha() and len(s) <= 5:
        return ASSET_STOCK
    return ASSET_CRYPTO


def profile_for_asset(asset_class: str) -> str:
    """The preset profile name that best covers an asset class (within the approved scope)."""
    return {ASSET_STOCK: "US Equities", ASSET_CRYPTO: "Binance"}.get(asset_class, "Generic")


def profile_for_symbol(symbol: str) -> str:
    """The profile name actually used to spec ``symbol`` — honest about the forex fallback.

    Forex has no broker preset in the approved scope, so :func:`spec_for_symbol` derives it from a
    class default rather than a profile; this reports that as ``"forex (default)"`` instead of
    misattributing it to the Generic (crypto) profile.
    """
    asset = infer_asset_class(symbol)
    if asset == ASSET_FOREX:
        return "forex (default)"
    return profile_for_asset(asset)


def spec_for_symbol(symbol: str, root: str | None = None) -> InstrumentSpec:
    """Resolve the best instrument spec for ``symbol`` without the caller naming a profile.

    Infers the asset class, then resolves through the matching preset (on disk under ``root`` if
    given, else in-memory). Forex symbols — which have no broker preset in the approved scope —
    fall back to a forex asset-class default so they still get 5-digit pricing.
    """
    asset = infer_asset_class(symbol)
    if asset == ASSET_FOREX:
        return default_spec_for(strip_postfix(symbol, ""), ASSET_FOREX)
    profile_name = profile_for_asset(asset)
    if root is not None:
        return resolve_spec(symbol, profile_name, root)
    return preset_profiles()[profile_name].resolve(symbol)
