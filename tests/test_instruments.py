"""Instrument metadata + broker profiles (the Data-Manager keystone).

Pure, Qt-free: an ``InstrumentSpec`` makes a downloaded series self-describing (tick / pip /
step / contract-size → correct price decimals, resampling, symbol mapping); a ``BrokerProfile``
bundles specs + a *display-only* timezone label (all bars stay UTC — no broker-TZ storage).
Storage mirrors the rollup-pins convention (JSON under ``<root>/profiles``).
"""

import json

from vike_trader_app.data import instruments as inst
from vike_trader_app.data.instruments import BrokerProfile, InstrumentSpec


# --- tick -> price decimals --------------------------------------------------------------

def test_decimals_for_tick_common_ticks():
    assert inst.decimals_for_tick(0.01) == 2        # crypto / equities cent
    assert inst.decimals_for_tick(0.00001) == 5     # FX 5-digit
    assert inst.decimals_for_tick(0.0001) == 4      # FX pip
    assert inst.decimals_for_tick(1) == 0           # whole-number tick
    assert inst.decimals_for_tick(0.5) == 1
    assert inst.decimals_for_tick(0.25) == 2        # futures-style quarter tick
    assert inst.decimals_for_tick(10) == 0


def test_decimals_for_tick_nonpositive_is_zero():
    assert inst.decimals_for_tick(0) == 0
    assert inst.decimals_for_tick(-1) == 0


# --- InstrumentSpec ----------------------------------------------------------------------

def test_spec_decimals_derived_from_tick_when_not_overridden():
    assert InstrumentSpec("BTCUSDT", tick_size=0.01).decimals == 2
    assert InstrumentSpec("EURUSD", tick_size=0.00001).decimals == 5


def test_spec_decimals_honours_explicit_override():
    assert InstrumentSpec("XYZ", tick_size=0.01, price_decimals=4).decimals == 4


def test_spec_format_price_rounds_to_decimals():
    assert InstrumentSpec("BTCUSDT", tick_size=0.01).format_price(12345.6789) == "12345.68"
    assert InstrumentSpec("EURUSD", tick_size=0.00001).format_price(1.234567) == "1.23457"
    assert InstrumentSpec("NKY", tick_size=1).format_price(38123.4) == "38123"


# --- preset profiles (approved scope: Binance / Bybit / Coinbase / US Equities / Generic) -

def test_preset_profiles_are_the_approved_five():
    names = set(inst.preset_profiles())
    assert names == {"Binance", "Bybit", "Coinbase", "US Equities", "Generic"}


def test_binance_preset_is_crypto_utc_with_known_btc_tick():
    p = inst.preset_profiles()["Binance"]
    assert p.asset_class == "crypto"
    assert p.timezone == "UTC"
    spec = p.resolve("BTCUSDT")
    assert spec.tick_size == 0.01
    assert spec.decimals == 2


def test_us_equities_preset_is_stock_ny_with_cent_tick():
    p = inst.preset_profiles()["US Equities"]
    assert p.asset_class == "stock"
    assert p.timezone == "America/New_York"      # label only; storage stays UTC
    spec = p.resolve("AAPL")
    assert spec.tick_size == 0.01 and spec.decimals == 2
    assert spec.volume_step == 1                 # whole shares


# --- BrokerProfile.resolve ---------------------------------------------------------------

def test_resolve_strips_profile_postfix_before_lookup():
    p = inst.preset_profiles()["Bybit"]
    assert p.postfix                              # Bybit carries a postfix
    bare = p.resolve("BTCUSDT")
    tagged = p.resolve("BTCUSDT" + p.postfix)
    assert bare.tick_size == tagged.tick_size     # postfix stripped -> same spec


def test_resolve_unknown_symbol_falls_back_to_class_default():
    p = inst.preset_profiles()["Binance"]
    spec = p.resolve("FOOBARUSDT")                # not in the known table
    assert spec.asset_class == "crypto"
    assert spec.symbol == "FOOBARUSDT"            # default carries the queried symbol
    assert spec.tick_size > 0


# --- JSON storage (mirrors rollup load_pins/save_pins) -----------------------------------

def test_save_then_load_profile_roundtrips(tmp_path):
    p = BrokerProfile(
        name="My Broker", timezone="Europe/London", asset_class="forex", postfix=".mb",
        instruments={"EURUSD": InstrumentSpec("EURUSD", "forex", 0.00001, pip_size=0.0001,
                                              contract_size=100_000, quote_ccy="USD", base_ccy="EUR")},
    )
    inst.save_profile(p, str(tmp_path))
    back = inst.load_profile("My Broker", str(tmp_path))
    assert back == p


def test_load_missing_profile_returns_none(tmp_path):
    assert inst.load_profile("Nope", str(tmp_path)) is None


def test_ensure_presets_writes_five_and_is_idempotent(tmp_path):
    written = inst.ensure_presets(str(tmp_path))
    assert set(written) == {"Binance", "Bybit", "Coinbase", "US Equities", "Generic"}
    assert set(inst.list_profiles(str(tmp_path))) == set(written)
    # second call writes nothing new (doesn't clobber / duplicate)
    assert inst.ensure_presets(str(tmp_path)) == []
    assert set(inst.list_profiles(str(tmp_path))) == set(written)


def test_ensure_presets_files_are_valid_json(tmp_path):
    inst.ensure_presets(str(tmp_path))
    for name in inst.list_profiles(str(tmp_path)):
        d = json.loads(inst.profile_path(str(tmp_path), name).read_text())
        assert d["name"] == name


def test_resolve_spec_via_storage(tmp_path):
    inst.ensure_presets(str(tmp_path))
    spec = inst.resolve_spec("BTCUSDT", "Binance", str(tmp_path))
    assert spec.tick_size == 0.01 and spec.decimals == 2


# --- symbol -> asset class / spec --------------------------------------------------------

def test_infer_asset_class():
    assert inst.infer_asset_class("BTCUSDT") == "crypto"
    assert inst.infer_asset_class("ETHUSD") == "crypto"
    assert inst.infer_asset_class("EURUSD") == "forex"     # both halves are currencies
    assert inst.infer_asset_class("AAPL") == "stock"
    assert inst.infer_asset_class("SPY") == "stock"


def test_spec_for_symbol_forex_gets_five_digits_without_a_preset():
    spec = inst.spec_for_symbol("EURUSD")                  # no FX broker preset in scope
    assert spec.asset_class == "forex"
    assert spec.tick_size == 0.00001 and spec.decimals == 5
    assert spec.pip_size == 0.0001


def test_spec_for_symbol_crypto_and_stock_in_memory():
    assert inst.spec_for_symbol("BTCUSDT").tick_size == 0.01
    aapl = inst.spec_for_symbol("AAPL")
    assert aapl.asset_class == "stock" and aapl.decimals == 2


def test_spec_for_symbol_uses_stored_presets_when_root_given(tmp_path):
    inst.ensure_presets(str(tmp_path))
    assert inst.spec_for_symbol("BTCUSDT", str(tmp_path)).decimals == 2


def test_profile_for_symbol_is_honest_about_forex_fallback():
    assert inst.profile_for_symbol("BTCUSDT") == "Binance"
    assert inst.profile_for_symbol("AAPL") == "US Equities"
    assert inst.profile_for_symbol("EURUSD") == "forex (default)"  # no FX preset in scope
