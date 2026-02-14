"""BaseViewModel — pure Python, no Qt dependency.

Provides subscription lifecycle management so that concrete ViewModels can
subscribe to ``EventBus`` events and have them cleaned up automatically via
``dispose()``.
"""

from __future__ import annotations

from typing import Callable, Type

from iPhoto.events.bus import EventBus, Subscription


class BaseViewModel:
    """ViewModel base class — pure Python, no Qt dependency."""

    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []

    def subscribe_event(
        self,
        event_bus: EventBus,
        event_type: Type,
        handler: Callable,
    ) -> Subscription:
        """Subscribe to an event type and track the subscription."""
        sub = event_bus.subscribe(event_type, handler)
        self._subscriptions.append(sub)
        return sub

    def dispose(self) -> None:
        """Cancel all tracked event subscriptions."""
        for sub in self._subscriptions:
            sub.cancel()
        self._subscriptions.clear()
