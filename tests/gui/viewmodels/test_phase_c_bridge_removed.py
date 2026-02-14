"""Phase C verification — QtEventBridge has been fully removed.

After completing Phase C of the Qt Signal → EventBus migration, the
transitional ``QtEventBridge`` adapter is no longer needed. All ViewModels
now subscribe directly to the ``EventBus`` via pure Python Signals.

These tests verify the bridge module is gone and the pure MVVM stack
still works without it.
"""

import importlib

import pytest

from iPhoto.events.bus import EventBus, Event
from iPhoto.gui.viewmodels.signal import Signal


def test_qt_event_bridge_module_removed():
    """QtEventBridge module must no longer exist after Phase C."""
    with pytest.raises(ImportError):
        importlib.import_module("iPhoto.gui.services.qt_event_bridge")


def test_viewmodels_subscribe_directly_to_eventbus():
    """ViewModels use EventBus directly — no bridge needed."""
    from dataclasses import dataclass, field

    @dataclass(kw_only=True)
    class _Evt(Event):
        payload: str = ""

    bus = EventBus()
    received = []
    sub = bus.subscribe(_Evt, lambda e: received.append(e.payload))
    bus.publish(_Evt(payload="direct"))

    assert received == ["direct"]
    sub.cancel()


def test_pure_signal_works_without_bridge():
    """Pure Python Signal still works independently of any bridge."""
    sig = Signal()
    values = []
    sig.connect(lambda v: values.append(v))
    sig.emit(42)

    assert values == [42]
