"""Unit tests for the file-backed trading journal."""

from vike_trader_app.analysis.journal import Journal, JournalEntry


def test_add_persists_and_reloads(tmp_path):
    path = str(tmp_path / "journal.json")
    j = Journal(path)
    j.add(JournalEntry(ts=1000, title="First", symbol="EURUSD", strategy="MaCrossover",
                       notes="trend looked clean"))
    j.add(JournalEntry(ts=2000, title="Second", symbol="BTCUSDT"))
    # a fresh Journal on the same path sees both entries
    j2 = Journal(path)
    assert len(j2.entries()) == 2
    assert j2.entries()[0].title == "Second"   # newest first
    assert j2.entries()[1].notes == "trend looked clean"


def test_remove(tmp_path):
    path = str(tmp_path / "journal.json")
    j = Journal(path)
    j.add(JournalEntry(ts=1, title="A"))
    j.add(JournalEntry(ts=2, title="B"))
    j.remove(0)                       # removes the first stored entry (A)
    titles = [e.title for e in Journal(path).entries()]
    assert titles == ["B"]


def test_corrupt_file_starts_clean(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text("not json {{{", encoding="utf-8")
    assert Journal(str(path)).entries() == []
