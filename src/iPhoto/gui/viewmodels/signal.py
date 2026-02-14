"""Pure Python signal system — no Qt dependency.

Provides ``Signal`` for observer-pattern callbacks and ``ObservableProperty``
for data-binding in ViewModels.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

_logger = logging.getLogger(__name__)


class Signal:
    """Pure Python signal — does not depend on Qt.

    Thread-safe: all handler mutations and emissions are protected by a lock.
    Exceptions raised by individual handlers are caught and logged so that one
    failing handler does not prevent subsequent handlers from executing (same
    semantics as ``EventBus``).
    """

    def __init__(self) -> None:
        self._handlers: list[Callable] = []
        self._lock = threading.Lock()

    def connect(self, handler: Callable) -> None:
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def disconnect(self, handler: Callable) -> None:
        with self._lock:
            self._handlers.remove(handler)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            handlers = list(self._handlers)
        for handler in handlers:
            try:
                handler(*args, **kwargs)
            except Exception as exc:
                _logger.error("Signal handler %r failed: %s", handler, exc)

    @property
    def handler_count(self) -> int:
        with self._lock:
            return len(self._handlers)


class ObservableProperty:
    """Observable property — ViewModel data-binding foundation.

    Emits ``changed(new_value, old_value)`` whenever the value is set to a
    different object.
    """

    def __init__(self, initial_value: Any = None) -> None:
        self._value = initial_value
        self.changed = Signal()

    @property
    def value(self) -> Any:
        return self._value

    @value.setter
    def value(self, new_value: Any) -> None:
        if self._value != new_value:
            old_value = self._value
            self._value = new_value
            self.changed.emit(new_value, old_value)
