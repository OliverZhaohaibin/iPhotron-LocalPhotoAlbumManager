"""QtEventBridge — bridges EventBus events to Qt signal slots.

Allows ViewModel-emitted events on the ``EventBus`` to be forwarded as Qt
signals so that existing Qt views can consume them without refactoring.
This is a transitional adapter: once all views bind directly to pure Python
Signals/ObservableProperties, the bridge can be removed.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Type

from iPhoto.events.bus import EventBus, Event, Subscription
from iPhoto.gui.viewmodels.signal import Signal


class QtEventBridge:
    """Forward ``EventBus`` events into pure-Python ``Signal`` instances.

    Typical usage (in a Coordinator or bootstrap)::

        bridge = QtEventBridge(event_bus)
        bridge.register(ScanCompletedEvent, my_qt_slot)
        # ... later ...
        bridge.dispose()
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._subscriptions: list[Subscription] = []
        self._bridges: Dict[Type, Signal] = {}
        self._logger = logging.getLogger(__name__)

    def register(
        self,
        event_type: Type[Event],
        handler: Callable,
    ) -> None:
        """Register *handler* to be called when *event_type* is published."""
        if event_type not in self._bridges:
            bridge_signal = Signal()
            self._bridges[event_type] = bridge_signal

            def _forwarder(event: Event) -> None:
                bridge_signal.emit(event)

            sub = self._event_bus.subscribe(event_type, _forwarder)
            self._subscriptions.append(sub)

        self._bridges[event_type].connect(handler)

    def unregister(self, event_type: Type[Event], handler: Callable) -> None:
        """Remove *handler* from *event_type* bridge."""
        if event_type in self._bridges:
            try:
                self._bridges[event_type].disconnect(handler)
            except ValueError:
                pass

    def dispose(self) -> None:
        """Cancel all EventBus subscriptions and clear bridges."""
        for sub in self._subscriptions:
            sub.cancel()
        self._subscriptions.clear()
        self._bridges.clear()
