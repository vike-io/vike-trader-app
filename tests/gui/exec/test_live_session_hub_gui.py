import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from vike_trader_app.ui.private_user_data import LiveExecutionSession  # noqa: E402


class _FakeHub:
    def __init__(self):
        self.bus = object()
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True


def test_hub_property_exposes_the_hub():
    hub = _FakeHub()
    sess = LiveExecutionSession(hub)
    assert sess.hub is hub


def test_hub_is_none_after_shutdown():
    hub = _FakeHub()
    sess = LiveExecutionSession(hub)
    sess.shutdown()
    assert sess.hub is None
    assert hub.shutdown_called is True
