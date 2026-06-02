from vike_trader_app.ui import icons


def test_news_icon_registered():
    assert "news" in icons._DRAW
    assert callable(icons._DRAW["news"])
