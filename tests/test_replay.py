"""Replay state-machine tests (Qt-free, drives the visual bar-by-bar replay)."""

from vike_trader_app.ui.replay import Replay


def test_starts_at_zero_and_paused():
    r = Replay(10)
    assert r.index == 0
    assert r.playing is False


def test_step_advances_one_bar():
    r = Replay(3)
    r.step()
    assert r.index == 1


def test_step_clamps_at_last_bar():
    r = Replay(2)
    r.step()
    r.step()
    r.step()
    assert r.index == 1  # last valid index = n-1


def test_step_back():
    r = Replay(3)
    r.seek(2)
    r.step_back()
    assert r.index == 1


def test_play_and_pause():
    r = Replay(3)
    r.play()
    assert r.playing is True
    r.pause()
    assert r.playing is False


def test_seek_clamps_both_ends():
    r = Replay(5)
    r.seek(99)
    assert r.index == 4
    r.seek(-3)
    assert r.index == 0


def test_at_end():
    r = Replay(2)
    assert r.at_end is False
    r.step()
    assert r.at_end is True


def test_tick_advances_only_while_playing():
    r = Replay(3)
    r.tick()
    assert r.index == 0  # paused -> no move
    r.play()
    r.tick()
    assert r.index == 1


def test_tick_auto_pauses_at_end():
    r = Replay(2)
    r.play()
    r.tick()  # -> index 1 (end)
    assert r.at_end is True
    assert r.playing is False
