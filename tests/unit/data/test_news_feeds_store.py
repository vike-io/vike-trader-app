"""SavedFeedStore — saved news feeds in the app SQLite DB (state-in-DB rule), not JSON files.

Every test points BOTH seams (db_path + the legacy ``path``) inside tmp_path so a developer's
real app DB / legacy ``storage/news_feeds.json`` is never read, written, or swept by the suite.
"""
import json

from vike_trader_app.data.news import feeds_store as fs_mod
from vike_trader_app.data.news.feeds_store import SavedFeed, SavedFeedStore


def _store(tmp_path, legacy="feeds.json"):
    return SavedFeedStore(str(tmp_path / legacy), db_path=str(tmp_path / "app.sqlite"))


def test_add_persists_and_reloads(tmp_path):
    store = _store(tmp_path)
    store.add(SavedFeed(name="My crypto", market="crypto", providers=["CoinDesk"]))
    assert [f.name for f in _store(tmp_path).feeds()] == ["My crypto"]
    assert _store(tmp_path).feeds()[0].market == "crypto"


def test_add_same_name_replaces(tmp_path):
    store = _store(tmp_path, "f.json")
    store.add(SavedFeed(name="A", query="x"))
    store.add(SavedFeed(name="A", query="y"))
    assert len(store.feeds()) == 1 and store.feeds()[0].query == "y"


def test_remove(tmp_path):
    store = _store(tmp_path)
    store.add(SavedFeed(name="A"))
    store.add(SavedFeed(name="B"))
    store.remove("A")
    assert [f.name for f in _store(tmp_path).feeds()] == ["B"]


def test_load_tolerates_missing_or_corrupt(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    assert _store(tmp_path, "bad.json").feeds() == []
    assert path.exists()  # user-authored presets: an unreadable file is left for recovery


# --- one-time legacy JSON migration ---------------------------------------------------

def test_migrates_legacy_json_into_db_and_deletes_it(tmp_path):
    legacy = tmp_path / "feeds.json"
    legacy.write_text(json.dumps([
        {"name": "Macro", "market": "", "providers": [], "symbol": "",
         "query": "CPI", "follow_chart": False},
        {"name": "BTC", "market": "crypto", "providers": ["CoinDesk"], "symbol": "BTCUSDT",
         "query": "", "follow_chart": True},
    ]), encoding="utf-8")

    store = _store(tmp_path)              # first open sweeps the legacy file
    assert [f.name for f in store.feeds()] == ["Macro", "BTC"]   # file order kept
    assert store.feeds()[0].query == "CPI" and store.feeds()[1].symbol == "BTCUSDT"
    assert not legacy.exists()            # imported then deleted — the DB is truth now
    assert [f.name for f in _store(tmp_path).feeds()] == ["Macro", "BTC"]  # from the DB


def test_migration_db_rows_win_over_legacy_file(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.add(SavedFeed(name="A", query="from-db"))

    legacy = tmp_path / "feeds.json"
    legacy.write_text(json.dumps([
        {"name": "A", "market": "", "providers": [], "symbol": "",
         "query": "stale", "follow_chart": True},
        {"name": "B", "market": "", "providers": [], "symbol": "",
         "query": "", "follow_chart": True},
    ]), encoding="utf-8")
    monkeypatch.setattr(fs_mod, "_MIGRATED", set())  # force the sweep to re-run

    again = _store(tmp_path)
    by_name = {f.name: f for f in again.feeds()}
    assert by_name["A"].query == "from-db"   # the DB row was not clobbered
    assert "B" in by_name                    # the new legacy row was still imported
    assert not legacy.exists()
