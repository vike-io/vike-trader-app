"""SQLite instrument store — broker profiles + symbol catalog (state-in-DB P1).

Pins the binding semantics: the legacy ``<root>/profiles/*.json`` store migrates into
``<root>/db/instruments.sqlite`` exactly once (user edits preserved, files deleted), all
normal reads/writes hit the DB, JSON survives only as explicit export/import interchange,
and resolution prefers the exchange catalog over manual overrides over class defaults.
No network anywhere — the Binance refresh is exercised through its injectable ``fetch`` seam.
"""

import json

from vike_trader_app.data import instrument_db as idb
from vike_trader_app.data import instruments as inst
from vike_trader_app.data.instruments import BrokerProfile, InstrumentSpec


def _write_legacy_json(root, profile):
    """Author a profile in the OLD on-disk format — what a pre-migration install left behind."""
    path = inst.profile_path(str(root), profile.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inst.profile_to_dict(profile), indent=2))
    return path


def _edited_binance():
    """The Binance preset with a user edit (BTCUSDT tick 0.01 -> 0.5) — must survive migration."""
    p = inst.preset_profiles()["Binance"]
    return inst.with_instrument(p, InstrumentSpec("BTCUSDT", "crypto", tick_size=0.5))


# --- one-time legacy JSON migration -------------------------------------------------------

def test_migrate_imports_edited_jsons_then_deletes_files(tmp_path):
    _write_legacy_json(tmp_path, _edited_binance())
    _write_legacy_json(tmp_path, BrokerProfile("My FX", asset_class="forex", postfix=".fx"))

    imported = idb.migrate_json_profiles(str(tmp_path))

    assert imported == ["Binance", "My FX"]
    # the user's edit came through, and the legacy store is gone (state lives in the DB now)
    assert inst.load_profile("Binance", str(tmp_path)).instruments["BTCUSDT"].tick_size == 0.5
    assert inst.load_profile("My FX", str(tmp_path)).postfix == ".fx"
    assert not list(tmp_path.glob("profiles/*.json"))
    assert not (tmp_path / "profiles").exists()      # empty legacy dir is swept too


def test_migrate_is_idempotent_and_never_clobbers_db(tmp_path):
    _write_legacy_json(tmp_path, BrokerProfile("P", timezone="UTC"))
    idb.migrate_json_profiles(str(tmp_path))
    # the user edits P in the app (DB is now the source of truth)...
    inst.save_profile(BrokerProfile("P", timezone="Asia/Tokyo"), str(tmp_path))
    # ...then a stale legacy file reappears (backup restore, partial old install)
    stale = _write_legacy_json(tmp_path, BrokerProfile("P", timezone="Europe/Paris"))

    assert idb.migrate_json_profiles(str(tmp_path)) == []          # nothing imported
    assert inst.load_profile("P", str(tmp_path)).timezone == "Asia/Tokyo"  # DB edit wins
    assert not stale.exists()                                      # stale file still swept


def test_first_store_open_runs_migration_lazily(tmp_path):
    _write_legacy_json(tmp_path, BrokerProfile("Lazy", description="from json"))
    # no explicit migrate call — any store read triggers the sweep
    assert "Lazy" in inst.list_profiles(str(tmp_path))
    assert not (tmp_path / "profiles").exists()


def test_migrate_leaves_unreadable_file_in_place(tmp_path):
    good = _write_legacy_json(tmp_path, BrokerProfile("Good"))
    bad = tmp_path / "profiles" / "bad.json"
    bad.write_text("{this is not json")

    assert idb.migrate_json_profiles(str(tmp_path)) == ["Good"]
    assert not good.exists()      # imported -> deleted
    assert bad.exists()           # unreadable -> kept for manual recovery, never silently lost


def test_ensure_presets_after_legacy_edit_keeps_user_edit(tmp_path):
    """The app's startup path: migration must land BEFORE preset seeding can shadow it."""
    _write_legacy_json(tmp_path, _edited_binance())
    written = inst.ensure_presets(str(tmp_path))
    assert "Binance" not in written   # already migrated in -> not re-seeded
    assert inst.load_profile("Binance", str(tmp_path)).instruments["BTCUSDT"].tick_size == 0.5


# --- profile round-trip via the DB ---------------------------------------------------------

def test_save_load_roundtrip_via_db_with_no_json_files(tmp_path):
    p = BrokerProfile(
        name="My Broker", timezone="Europe/London", asset_class="forex", postfix=".mb",
        description="round trip",
        instruments={"EURUSD": InstrumentSpec("EURUSD", "forex", 0.00001, pip_size=0.0001,
                                              contract_size=100_000, quote_ccy="USD",
                                              base_ccy="EUR")},
        default_spec=inst.default_spec_for("", "forex"),
    )
    inst.save_profile(p, str(tmp_path))
    assert inst.load_profile("My Broker", str(tmp_path)) == p
    assert idb.db_path(str(tmp_path)).is_file()       # state in storage/db/instruments.sqlite
    assert not list(tmp_path.rglob("*.json"))         # ...and in NO loose JSON file


# --- symbol catalog -------------------------------------------------------------------------

def test_catalog_upsert_lookup_and_update(tmp_path):
    root = str(tmp_path)
    n = idb.catalog_upsert(root, "binance", [
        InstrumentSpec("BTCUSDT", "crypto", 0.01, pip_size=0.01, volume_step=0.00001,
                       quote_ccy="USDT", base_ccy="BTC"),
        InstrumentSpec("ETHUSDT", "crypto", 0.01, pip_size=0.01, volume_step=0.0001,
                       quote_ccy="USDT", base_ccy="ETH"),
    ])
    assert n == 2

    hit = idb.catalog_lookup(root, "binance", "BTCUSDT")
    assert hit.tick_size == 0.01 and hit.volume_step == 0.00001
    assert hit.base_ccy == "BTC" and hit.quote_ccy == "USDT"
    # keys are case-folded both ways (exchange lower, symbol upper)
    assert idb.catalog_lookup(root, "BINANCE", "btcusdt") == hit
    assert idb.catalog_lookup(root, "binance", "NOPEUSDT") is None
    assert idb.catalog_lookup(root, "kraken", "BTCUSDT") is None   # per-exchange isolation

    # re-upsert replaces in place (refresh semantics), never duplicates
    idb.catalog_upsert(root, "binance", [InstrumentSpec("BTCUSDT", "crypto", 0.1)])
    assert idb.catalog_lookup(root, "binance", "BTCUSDT").tick_size == 0.1


# --- Binance /exchangeInfo refresh through the injectable fetch seam ------------------------

_EXCHANGE_INFO = {
    "timezone": "UTC",
    "serverTime": 1_765_000_000_000,
    "symbols": [
        {
            "symbol": "BTCUSDT", "status": "TRADING",
            "baseAsset": "BTC", "quoteAsset": "USDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.01000000",
                 "maxPrice": "1000000.00000000", "tickSize": "0.01000000"},
                {"filterType": "LOT_SIZE", "minQty": "0.00001000",
                 "maxQty": "9000.00000000", "stepSize": "0.00001000"},
                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
            ],
        },
        {
            "symbol": "DOGEUSDT", "status": "TRADING",
            "baseAsset": "DOGE", "quoteAsset": "USDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.00001000",
                 "maxPrice": "1000.00000000", "tickSize": "0.00001000"},
                {"filterType": "LOT_SIZE", "minQty": "1.00000000",
                 "maxQty": "9000000.00000000", "stepSize": "1.00000000"},
            ],
        },
        {   # no PRICE_FILTER -> can't price -> skipped, not imported as garbage
            "symbol": "BROKEN", "status": "BREAK",
            "baseAsset": "BRK", "quoteAsset": "USDT",
            "filters": [{"filterType": "LOT_SIZE", "stepSize": "1.00000000"}],
        },
    ],
}


def test_refresh_binance_catalog_with_fake_fetch(tmp_path):
    root = str(tmp_path)
    n = idb.refresh_binance_catalog(root, fetch=lambda: _EXCHANGE_INFO)
    assert n == 2                                     # BROKEN skipped

    btc = idb.catalog_lookup(root, "binance", "BTCUSDT")
    assert btc.tick_size == 0.01 and btc.volume_step == 0.00001
    assert btc.base_ccy == "BTC" and btc.quote_ccy == "USDT"
    assert btc.decimals == 2                          # derived from the parsed tick

    doge = idb.catalog_lookup(root, "binance", "DOGEUSDT")
    assert doge.tick_size == 0.00001 and doge.volume_step == 1.0
    assert doge.decimals == 5

    assert idb.catalog_lookup(root, "binance", "BROKEN") is None


def test_parse_exchange_info_is_pure_and_tolerant():
    specs = idb.parse_exchange_info(_EXCHANGE_INFO)
    assert [s.symbol for s in specs] == ["BTCUSDT", "DOGEUSDT"]
    assert idb.parse_exchange_info({}) == []          # empty/odd payloads degrade to nothing


# --- resolution order: catalog -> manual override -> asset-class default --------------------

def test_resolve_spec_order_catalog_then_override_then_default(tmp_path):
    root = str(tmp_path)
    profile = BrokerProfile(
        name="TestEx", asset_class="crypto", postfix=".x",
        instruments={
            "AAAUSDT": InstrumentSpec("AAAUSDT", "crypto", tick_size=0.5),    # manual override
            "CCCUSDT": InstrumentSpec("CCCUSDT", "crypto", tick_size=0.25),   # override only
        },
    )
    inst.save_profile(profile, root)
    idb.catalog_upsert(root, "testex", [          # exchange key = profile slug
        InstrumentSpec("AAAUSDT", "crypto", tick_size=0.001),
        InstrumentSpec("BBBUSDT", "crypto", tick_size=0.005),
    ])

    # 1. catalog beats the manual override (venue-refreshed spec is authoritative)
    assert inst.resolve_spec("AAAUSDT", "TestEx", root).tick_size == 0.001
    # ...and the broker postfix is stripped before the catalog lookup
    assert inst.resolve_spec("AAAUSDT.x", "TestEx", root).tick_size == 0.001
    # 2. catalog also specs symbols the profile never listed
    assert inst.resolve_spec("BBBUSDT", "TestEx", root).tick_size == 0.005
    # 3. no catalog row -> the manual override layer
    assert inst.resolve_spec("CCCUSDT", "TestEx", root).tick_size == 0.25
    # 4. neither -> the asset-class default (original behavior, preserved)
    fallback = inst.resolve_spec("DDDUSDT", "TestEx", root)
    assert fallback.tick_size == 0.01 and fallback.volume_step == 0.00001
    assert fallback.symbol == "DDDUSDT"


def test_broker_profile_resolve_accepts_injected_catalog():
    """The dataclass layer alone honors the order — no DB needed (pure seam)."""
    p = BrokerProfile("X", instruments={"AAA": InstrumentSpec("AAA", "crypto", 0.5)})
    catalog = {"AAA": InstrumentSpec("AAA", "crypto", 0.001)}.get
    assert p.resolve("AAA", catalog=catalog).tick_size == 0.001   # catalog first
    assert p.resolve("AAA").tick_size == 0.5                      # without it: override
    assert p.resolve("ZZZ", catalog=catalog).symbol == "ZZZ"      # miss falls through


# --- export / import: the only sanctioned JSON path ------------------------------------------

def test_export_import_json_roundtrip(tmp_path):
    root_a, root_b = str(tmp_path / "a"), str(tmp_path / "b")
    p = BrokerProfile("Shared", timezone="UTC", postfix=".sh",
                      instruments={"BTCUSDT": InstrumentSpec("BTCUSDT", "crypto", 0.5)})
    inst.save_profile(p, root_a)

    out = inst.export_profile_json("Shared", root_a, tmp_path / "out" / "shared.json")
    assert out.is_file()
    assert json.loads(out.read_text())["name"] == "Shared"        # plain interchange JSON

    # import on another machine/root reproduces the profile exactly
    assert inst.import_profile_json(out, root_b) == p
    assert inst.load_profile("Shared", root_b) == p
    assert out.is_file()                                          # user's file is never consumed

    # importing over a later edit restores the snapshot (explicit user action may overwrite)
    inst.save_profile(BrokerProfile("Shared", timezone="Asia/Tokyo"), root_a)
    inst.import_profile_json(out, root_a)
    assert inst.load_profile("Shared", root_a) == p


def test_export_unknown_profile_raises(tmp_path):
    try:
        inst.export_profile_json("Ghost", str(tmp_path), tmp_path / "x.json")
    except ValueError as exc:
        assert "Ghost" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("expected ValueError for unknown profile")
