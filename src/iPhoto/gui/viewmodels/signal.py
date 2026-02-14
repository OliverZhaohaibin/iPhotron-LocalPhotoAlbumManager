"""Pure Python signal system — no Qt dependency.

Provides ``Signal`` for observer-pattern callbacks and ``ObservableProperty``
for data-binding in ViewModels.
"""

from __future__ import annotations

from typing import Any, Callable


class Signal:
    """Pure Python signal — does not depend on Qt."""

    def __init__(self) -> None:
        self._handlers: list[Callable] = []

    def connect(self, handler: Callable) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def disconnect(self, handler: Callable) -> None:
        self._handlers.remove(handler)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        for handler in list(self._handlers):
            handler(*args, **kwargs)

    @property
    def handler_count(self) -> int:
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
