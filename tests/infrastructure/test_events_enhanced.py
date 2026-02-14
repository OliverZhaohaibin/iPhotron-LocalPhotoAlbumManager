"""Enhanced tests for the EventBus (Phase 1/2 refactoring)."""

import pytest
import threading
from dataclasses import dataclass
from iPhoto.events.bus import Event, EventBus, Subscription
from iPhoto.events.domain_events import DomainEvent
from iPhoto.events.album_events import (
    AlbumOpenedEvent,
    ScanCompletedEvent,
    ScanProgressEvent,
    AssetImportedEvent,
    ThumbnailReadyEvent,
)


@dataclass(kw_only=True)
class _TestEvent(Event):
    value: int = 0


@dataclass(kw_only=True)
class _OtherEvent(Event):
    label: str = ""


def test_subscribe_returns_subscription():
    bus = EventBus()
    sub = bus.subscribe(_TestEvent, lambda e: None)
    assert isinstance(sub, Subscription)
    assert sub.active is True
    bus.shutdown()


def test_unsubscribe_removes_handler():
    bus = EventBus()
    called = []
    sub = bus.subscribe(_TestEvent, lambda e: called.append(1))
    bus.unsubscribe(sub)
    bus.publish(_TestEvent(value=1))
    assert len(called) == 0
    bus.shutdown()


def test_subscription_cancel():
    bus = EventBus()
    called = []
    sub = bus.subscribe(_TestEvent, lambda e: called.append(1))
    sub.cancel()
    bus.publish(_TestEvent(value=1))
    assert len(called) == 0
    bus.shutdown()


def test_publish_async_returns_futures():
    bus = EventBus()
    bus.subscribe(_TestEvent, lambda e: None)
    futures = bus.publish_async(_TestEvent(value=1))
    assert isinstance(futures, list)
    assert len(futures) == 1
    for f in futures:
        f.result(timeout=5)
    bus.shutdown()


def test_publish_async_executes_handlers():
    bus = EventBus()
    called = threading.Event()
    bus.subscribe(_TestEvent, lambda e: called.set())
    futures = bus.publish_async(_TestEvent(value=42))
    for f in futures:
        f.result(timeout=5)
    assert called.is_set()
    bus.shutdown()


def test_domain_event_has_defaults():
    ev = DomainEvent()
    assert ev.event_id  # non-empty string
    assert ev.timestamp is not None
    assert ev.source == ""


def test_album_opened_event():
    ev = AlbumOpenedEvent(album_id="a1", album_path="/tmp/album")
    assert ev.album_id == "a1"
    assert ev.album_path == "/tmp/album"
    assert ev.event_id  # inherited default


def test_scan_completed_event():
    ev = ScanCompletedEvent(album_id="a2", asset_count=10, duration_seconds=1.5)
    assert ev.album_id == "a2"
    assert ev.asset_count == 10
    assert ev.duration_seconds == 1.5


def test_asset_imported_event():
    ev = AssetImportedEvent(asset_ids=["x", "y"], album_id="a3")
    assert ev.asset_ids == ["x", "y"]
    assert ev.album_id == "a3"


def test_multiple_event_types_independent():
    bus = EventBus()
    test_calls = []
    other_calls = []
    bus.subscribe(_TestEvent, lambda e: test_calls.append(1))
    bus.subscribe(_OtherEvent, lambda e: other_calls.append(1))
    bus.publish(_TestEvent(value=1))
    assert len(test_calls) == 1
    assert len(other_calls) == 0
    bus.shutdown()


def test_handler_error_does_not_propagate():
    bus = EventBus()
    results = []

    def bad_handler(e):
        raise RuntimeError("boom")

    def good_handler(e):
        results.append(e.value)

    bus.subscribe(_TestEvent, bad_handler)
    bus.subscribe(_TestEvent, good_handler)
    bus.publish(_TestEvent(value=99))
    assert results == [99]
    bus.shutdown()
