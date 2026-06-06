from vike_trader_app.data.news.feeds_store import SavedFeed, SavedFeedStore


def test_add_persists_and_reloads(tmp_path):
    path = str(tmp_path / "feeds.json")
    store = SavedFeedStore(path)
    store.add(SavedFeed(name="My crypto", market="crypto", providers=["CoinDesk"]))
    assert [f.name for f in SavedFeedStore(path).feeds()] == ["My crypto"]
    assert SavedFeedStore(path).feeds()[0].market == "crypto"


def test_add_same_name_replaces(tmp_path):
    path = str(tmp_path / "f.json")
    store = SavedFeedStore(path)
    store.add(SavedFeed(name="A", query="x"))
    store.add(SavedFeed(name="A", query="y"))
    assert len(store.feeds()) == 1 and store.feeds()[0].query == "y"


def test_remove(tmp_path):
    path = str(tmp_path / "feeds.json")
    store = SavedFeedStore(path)
    store.add(SavedFeed(name="A"))
    store.add(SavedFeed(name="B"))
    store.remove("A")
    assert [f.name for f in SavedFeedStore(path).feeds()] == ["B"]


def test_load_tolerates_missing_or_corrupt(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    assert SavedFeedStore(str(path)).feeds() == []
