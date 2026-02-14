"""Tests for the pure Python Signal and ObservableProperty classes.

These tests run without Qt — validating the MVVM signal foundation.
"""

import pytest
from iPhoto.gui.viewmodels.signal import Signal, ObservableProperty


# ---------------------------------------------------------------------------
# Signal tests
# ---------------------------------------------------------------------------

class TestSignal:
    def test_connect_and_emit(self):
        sig = Signal()
        received = []
        sig.connect(lambda v: received.append(v))

        sig.emit(42)

        assert received == [42]

    def test_multiple_handlers(self):
        sig = Signal()
        a, b = [], []
        sig.connect(lambda v: a.append(v))
        sig.connect(lambda v: b.append(v))

        sig.emit("hello")

        assert a == ["hello"]
        assert b == ["hello"]

    def test_disconnect(self):
        sig = Signal()
        received = []
        handler = lambda v: received.append(v)
        sig.connect(handler)
        sig.emit(1)
        sig.disconnect(handler)
        sig.emit(2)

        assert received == [1]

    def test_disconnect_missing_raises(self):
        sig = Signal()
        with pytest.raises(ValueError):
            sig.disconnect(lambda: None)

    def test_emit_no_handlers(self):
        sig = Signal()
        sig.emit("no-op")  # should not raise

    def test_handler_count(self):
        sig = Signal()
        assert sig.handler_count == 0
        handler = lambda: None
        sig.connect(handler)
        assert sig.handler_count == 1
        sig.disconnect(handler)
        assert sig.handler_count == 0

    def test_emit_multiple_args(self):
        sig = Signal()
        received = []
        sig.connect(lambda *args: received.append(args))

        sig.emit(1, "two", 3.0)

        assert received == [(1, "two", 3.0)]

    def test_duplicate_connect_ignored(self):
        sig = Signal()
        handler = lambda: None
        sig.connect(handler)
        sig.connect(handler)
        assert sig.handler_count == 1

    def test_handler_exception_does_not_break_others(self):
        sig = Signal()
        received = []

        def bad_handler(v):
            raise RuntimeError("boom")

        sig.connect(bad_handler)
        sig.connect(lambda v: received.append(v))

        sig.emit(1)  # exception is caught; second handler still runs

        assert received == [1]


# ---------------------------------------------------------------------------
# ObservableProperty tests
# ---------------------------------------------------------------------------

class TestObservableProperty:
    def test_initial_value(self):
        prop = ObservableProperty(10)
        assert prop.value == 10

    def test_default_none(self):
        prop = ObservableProperty()
        assert prop.value is None

    def test_changed_emits_on_new_value(self):
        prop = ObservableProperty(0)
        changes = []
        prop.changed.connect(lambda new, old: changes.append((new, old)))

        prop.value = 5

        assert changes == [(5, 0)]

    def test_no_emit_when_same_value(self):
        prop = ObservableProperty("hello")
        changes = []
        prop.changed.connect(lambda new, old: changes.append((new, old)))

        prop.value = "hello"

        assert changes == []

    def test_multiple_changes(self):
        prop = ObservableProperty(0)
        changes = []
        prop.changed.connect(lambda new, old: changes.append((new, old)))

        prop.value = 1
        prop.value = 2
        prop.value = 2  # same — should not fire
        prop.value = 3

        assert changes == [(1, 0), (2, 1), (3, 2)]

    def test_list_value(self):
        prop = ObservableProperty([])
        changes = []
        prop.changed.connect(lambda new, old: changes.append(len(new)))

        prop.value = [1, 2, 3]

        assert changes == [3]
