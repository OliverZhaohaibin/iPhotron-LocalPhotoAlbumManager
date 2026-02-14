"""Tests for QtEventBridge — pure Python, no Qt dependency needed."""

from dataclasses import dataclass, field
from iPhoto.events.bus import EventBus, Event
from iPhoto.gui.services.qt_event_bridge import QtEventBridge


@dataclass(kw_only=True)
class _TestEvent(Event):
    data: str = ""


@dataclass(kw_only=True)
class _OtherEvent(Event):
    value: int = 0


class TestQtEventBridge:
    def test_register_and_receive(self):
        bus = EventBus()
        bridge = QtEventBridge(bus)
        received = []

        bridge.register(_TestEvent, lambda e: received.append(e.data))
        bus.publish(_TestEvent(data="hello"))

        assert received == ["hello"]

    def test_multiple_handlers(self):
        bus = EventBus()
        bridge = QtEventBridge(bus)
        a, b = [], []

        bridge.register(_TestEvent, lambda e: a.append(e.data))
        bridge.register(_TestEvent, lambda e: b.append(e.data))
        bus.publish(_TestEvent(data="x"))

        assert a == ["x"]
        assert b == ["x"]

    def test_different_event_types(self):
        bus = EventBus()
        bridge = QtEventBridge(bus)
        texts, numbers = [], []

        bridge.register(_TestEvent, lambda e: texts.append(e.data))
        bridge.register(_OtherEvent, lambda e: numbers.append(e.value))

        bus.publish(_TestEvent(data="a"))
        bus.publish(_OtherEvent(value=42))

        assert texts == ["a"]
        assert numbers == [42]

    def test_unregister(self):
        bus = EventBus()
        bridge = QtEventBridge(bus)
        received = []
        handler = lambda e: received.append(e.data)

        bridge.register(_TestEvent, handler)
        bus.publish(_TestEvent(data="before"))
        bridge.unregister(_TestEvent, handler)
        bus.publish(_TestEvent(data="after"))

        assert received == ["before"]

    def test_dispose(self):
        bus = EventBus()
        bridge = QtEventBridge(bus)
        received = []

        bridge.register(_TestEvent, lambda e: received.append(e.data))
        bus.publish(_TestEvent(data="before"))
        bridge.dispose()
        bus.publish(_TestEvent(data="after"))

        assert received == ["before"]

    def test_unregister_nonexistent_type(self):
        bus = EventBus()
        bridge = QtEventBridge(bus)
        # Should not raise
        bridge.unregister(_TestEvent, lambda e: None)

    def test_dispose_idempotent(self):
        bus = EventBus()
        bridge = QtEventBridge(bus)
        bridge.register(_TestEvent, lambda e: None)
        bridge.dispose()
        bridge.dispose()  # second call should not raise
