"""Unit tests for the Qt-free WorkspaceStore (Phase 4 named workspaces)."""

import json

from vike_trader_app.ui.session import SessionState
from vike_trader_app.ui.workspaces import (
    BUILTIN_NAMES,
    WorkspaceStore,
    builtin_workspaces,
    workspace_from_agent_spec,
)


def test_agent_spec_builds_session():
    spec = {"space": "chart", "watchlist_link": 2, "panels": {"market": True},
            "documents": [
                {"symbol": "btcusdt", "interval": "4h", "link_group": 2, "indicators": ["rsi"]},
                {"symbol": "ETHUSDT"}]}
    st = workspace_from_agent_spec(spec)
    assert st.space == 0
    assert [d["symbol"] for d in st.documents] == ["BTCUSDT", "ETHUSDT"]   # uppercased
    assert st.documents[0]["interval"] == "4h" and st.documents[0]["link_group"] == 2
    assert st.documents[0]["indicators"] == [{"name": "rsi"}]
    assert st.documents[1]["interval"] == "1h"                            # default applied
    assert st.panels["market"] is True and st.panels["backtester"] is True
    assert st.watchlist_link == 2


def test_agent_spec_is_defensive_about_llm_garbage():
    spec = {"space": "bogus", "watchlist_link": -5,
            "documents": [{"interval": "1h"},                              # no symbol -> skip
                          {"symbol": "ADAUSDT", "interval": "nope", "link_group": 99}]}
    st = workspace_from_agent_spec(spec)
    assert st.space == 0                                                   # unknown space -> Chart
    assert [d["symbol"] for d in st.documents] == ["ADAUSDT"]              # symbol-less dropped
    assert st.documents[0]["interval"] == "1h"                            # bad interval -> default
    assert st.documents[0]["link_group"] == 0                             # 99 clamped
    assert st.watchlist_link == 0                                         # -5 clamped


def test_agent_spec_empty_is_safe():
    st = workspace_from_agent_spec({})
    assert st.documents == [] and st.space == 0


def test_builtins_available_without_a_file(tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces.json")
    assert store.names() == BUILTIN_NAMES
    assert all(store.is_builtin(n) for n in BUILTIN_NAMES)
    trading = store.load("Trading")
    assert isinstance(trading, SessionState)
    assert trading.panels.get("market") is True


def test_research_builtin_has_two_linked_docs():
    research = builtin_workspaces()["Research"]
    assert [d["symbol"] for d in research.documents] == ["ETHUSDT", "SOLUSDT"]
    assert research.watchlist_link == 3
    assert all(d["link_group"] == 3 for d in research.documents)


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "workspaces.json"
    store = WorkspaceStore(path)
    st = SessionState(space=2, watchlist_link=4,
                      documents=[{"symbol": "ADAUSDT", "interval": "4h",
                                  "link_group": 4, "indicators": []}])
    assert store.save("My Desk", st)

    reopened = WorkspaceStore(path)            # persisted across instances
    assert "My Desk" in reopened.names()
    loaded = reopened.load("My Desk")
    assert loaded.space == 2 and loaded.watchlist_link == 4
    assert loaded.documents[0]["symbol"] == "ADAUSDT"


def test_saved_entry_has_no_inner_version_key(tmp_path):
    """The schema version belongs in the outer envelope, not inside each workspace entry."""
    path = tmp_path / "workspaces.json"
    WorkspaceStore(path).save("Desk", SessionState(space=1))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1                       # envelope keeps the version
    assert "version" not in raw["workspaces"]["Desk"]


def test_user_workspace_overrides_builtin_then_revert(tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save("Trading", SessionState(space=1))      # shadow the built-in
    assert store.load("Trading").space == 1
    assert store.names().count("Trading") == 1        # not duplicated
    store.delete("Trading")                           # revert to built-in
    assert store.load("Trading").space == 0


def test_delete_only_affects_user_workspaces(tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces.json")
    assert store.delete("Trading") is False           # a pure built-in can't be deleted
    store.save("Scratch", SessionState())
    assert store.delete("Scratch") is True
    assert "Scratch" not in store.names()


def test_rename_user_workspace(tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save("Old", SessionState(space=3))
    assert store.rename("Old", "New")
    assert "New" in store.names() and "Old" not in store.names()
    assert store.load("New").space == 3
    assert store.rename("New", "Trading") is False    # can't collide with an existing name


def test_corrupt_file_falls_back_to_builtins(tmp_path):
    path = tmp_path / "workspaces.json"
    path.write_text("{ not valid json", encoding="utf-8")
    store = WorkspaceStore(path)
    assert store.names() == BUILTIN_NAMES


def test_names_lists_user_after_builtins(tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save("Zeta", SessionState())
    store.save("Alpha", SessionState())
    assert store.names() == BUILTIN_NAMES + ["Zeta", "Alpha"]   # insertion order after builtins
