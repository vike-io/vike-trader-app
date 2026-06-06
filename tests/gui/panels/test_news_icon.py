import pytest

pytest.importorskip("PySide6")  # icons.py imports PySide6; skip on the non-UI CI job

from vike_trader_app.ui import icons  # noqa: E402


def test_news_icon_registered():
    assert "news" in icons._DRAW
    assert callable(icons._DRAW["news"])
