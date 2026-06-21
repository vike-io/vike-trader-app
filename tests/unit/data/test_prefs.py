"""App preferences persist in the app SQLite DB (round-trip + default fallback)."""

from vike_trader_app.data import prefs


def test_pref_default_when_unset(tmp_path):
    db = tmp_path / "db" / "vike_trader_app.sqlite"
    assert prefs.get_pref("optimizer_workers", 0, db_path=db) == 0
    assert prefs.get_pref("missing", "fallback", db_path=db) == "fallback"


def test_pref_set_get_roundtrip(tmp_path):
    db = tmp_path / "db" / "vike_trader_app.sqlite"
    prefs.set_pref("optimizer_workers", 8, db_path=db)
    assert prefs.get_pref("optimizer_workers", 0, db_path=db) == 8

    # second key in the same single-row document, and overwrite the first
    prefs.set_pref("theme", "dark", db_path=db)
    prefs.set_pref("optimizer_workers", 0, db_path=db)
    assert prefs.get_pref("theme", None, db_path=db) == "dark"
    assert prefs.get_pref("optimizer_workers", -1, db_path=db) == 0
