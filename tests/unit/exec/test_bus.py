"""Re-entrant EventBus: fan-out, and the load-bearing 'nested publish is delivered, not dropped'."""

from vike_trader_app.exec.bus import EventBus


def test_fans_out_to_all_subscribers():
    bus = EventBus()
    a, b = [], []
    bus.subscribe(a.append)
    bus.subscribe(b.append)
    bus.publish("e1")
    assert a == ["e1"] and b == ["e1"]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    bus.unsubscribe(seen.append)  # same bound method identity? -> use explicit handler below
    h = seen.append
    bus.subscribe(h)
    bus.unsubscribe(h)
    bus.publish("e1")
    assert seen == []


def test_reentrant_publish_is_delivered_not_dropped():
    # THE load-bearing test: a subscriber that re-publishes during dispatch (an OCO sibling-cancel
    # or a RiskGate auto-cancel reacting to a fill) must have its event delivered to ALL subscribers,
    # AFTER the current event finishes its fan-out (FIFO). A SymbolLinkBus-style suppress-guard would
    # silently drop 'second'.
    bus = EventBus()
    seen = []

    def a(ev):
        seen.append(("A", ev))
        if ev == "first":
            bus.publish("second")  # nested publish during the 'first' fan-out

    def b(ev):
        seen.append(("B", ev))

    bus.subscribe(a)
    bus.subscribe(b)
    bus.publish("first")
    assert seen == [("A", "first"), ("B", "first"), ("A", "second"), ("B", "second")]


def test_draining_flag_resets_so_bus_is_reusable_after_publish():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    bus.publish("e1")
    bus.publish("e2")
    assert seen == ["e1", "e2"]
