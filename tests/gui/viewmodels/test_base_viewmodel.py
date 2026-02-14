"""Tests for BaseViewModel — pure Python, no Qt dependency."""

from iPhoto.events.bus import EventBus, Event
from iPhoto.gui.viewmodels.base import BaseViewModel
from dataclasses import dataclass, field


@dataclass(kw_only=True)
class _FakeEvent(Event):
    payload: str = ""


class TestBaseViewModel:
    def test_subscribe_event_receives_events(self):
        bus = EventBus()
        vm = BaseViewModel()
        received = []

        vm.subscribe_event(bus, _FakeEvent, lambda e: received.append(e.payload))
        bus.publish(_FakeEvent(payload="hello"))

        assert received == ["hello"]

    def test_dispose_cancels_subscriptions(self):
        bus = EventBus()
        vm = BaseViewModel()
        received = []

        vm.subscribe_event(bus, _FakeEvent, lambda e: received.append(e.payload))
        bus.publish(_FakeEvent(payload="before"))
        vm.dispose()
        bus.publish(_FakeEvent(payload="after"))

        assert received == ["before"]

    def test_dispose_clears_subscription_list(self):
        bus = EventBus()
        vm = BaseViewModel()
        vm.subscribe_event(bus, _FakeEvent, lambda e: None)
        assert len(vm._subscriptions) == 1

        vm.dispose()
        assert len(vm._subscriptions) == 0

    def test_multiple_subscriptions(self):
        bus = EventBus()
        vm = BaseViewModel()
        a, b = [], []

        vm.subscribe_event(bus, _FakeEvent, lambda e: a.append(e.payload))
        vm.subscribe_event(bus, _FakeEvent, lambda e: b.append(e.payload))
        bus.publish(_FakeEvent(payload="x"))

        assert a == ["x"]
        assert b == ["x"]

    def test_subscribe_returns_subscription(self):
        bus = EventBus()
        vm = BaseViewModel()
        sub = vm.subscribe_event(bus, _FakeEvent, lambda e: None)

        assert sub is not None
        assert sub.active is True
