import pytest
import time
from dataclasses import dataclass
from iPhoto.events.bus import Event, EventBus

@dataclass(kw_only=True)
class SimpleEvent(Event):
    payload: str = ""

def test_sync_subscribe_publish():
    bus = EventBus()
    received = []

    def handler(event: SimpleEvent):
        received.append(event.payload)

    bus.subscribe(SimpleEvent, handler)
    bus.publish(SimpleEvent(payload="hello"))

    assert len(received) == 1
    assert received[0] == "hello"

def test_async_subscribe_publish():
    bus = EventBus()
    received = []

    def handler(event: SimpleEvent):
        time.sleep(0.1)
        received.append(event.payload)

    bus.subscribe(SimpleEvent, handler, async_=True)
    bus.publish(SimpleEvent(payload="world"))

    # Wait for async execution
    time.sleep(0.2)

    assert len(received) == 1
    assert received[0] == "world"
    bus.shutdown()

def test_multiple_handlers():
    bus = EventBus()
    count = 0

    def handler1(event):
        nonlocal count
        count += 1

    def handler2(event):
        nonlocal count
        count += 2

    bus.subscribe(SimpleEvent, handler1)
    bus.subscribe(SimpleEvent, handler2)

    bus.publish(SimpleEvent())

    assert count == 3
