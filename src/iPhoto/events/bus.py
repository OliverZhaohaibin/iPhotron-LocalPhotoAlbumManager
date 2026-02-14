import logging
import threading
import uuid
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Type


@dataclass(kw_only=True)
class Event:
    """Base event class (kept for backward compatibility)."""
    timestamp: datetime = field(default_factory=datetime.now)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class Subscription:
    """Handle returned by subscribe(); can be used to unsubscribe."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: Type = Event
    handler: Callable = field(default=lambda e: None)
    active: bool = True

    def cancel(self):
        self.active = False


class EventBus:
    def __init__(self, logger: logging.Logger = None):
        self._logger = logger or logging.getLogger(__name__)
        self._sync_handlers: Dict[Type[Event], List[Subscription]] = defaultdict(list)
        self._async_handlers: Dict[Type[Event], List[Subscription]] = defaultdict(list)
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._lock = threading.Lock()

    def subscribe(self, event_type: Type[Event], handler: Callable, async_: bool = False) -> Subscription:
        sub = Subscription(event_type=event_type, handler=handler)
        with self._lock:
            if async_:
                self._async_handlers[event_type].append(sub)
            else:
                self._sync_handlers[event_type].append(sub)
        return sub

    def unsubscribe(self, subscription: Subscription):
        subscription.active = False
        with self._lock:
            for store in (self._sync_handlers, self._async_handlers):
                for subs in store.values():
                    try:
                        subs.remove(subscription)
                    except ValueError:
                        pass

    def publish(self, event: Event):
        event_type = type(event)

        with self._lock:
            sync_subs = list(self._sync_handlers[event_type])
            async_subs = list(self._async_handlers[event_type])

        # Synchronous handlers
        for sub in sync_subs:
            if not sub.active:
                continue
            try:
                sub.handler(event)
            except Exception as e:
                self._logger.error(f"Sync handler failed for {event_type.__name__}: {e}")

        # Asynchronous handlers
        for sub in async_subs:
            if not sub.active:
                continue
            self._executor.submit(self._safe_async_call, sub.handler, event)

    def publish_async(self, event: Event) -> List[Future]:
        """Submit all handlers (sync and async) to the thread pool, return futures."""
        event_type = type(event)
        futures: List[Future] = []

        with self._lock:
            sync_subs = list(self._sync_handlers[event_type])
            async_subs = list(self._async_handlers[event_type])

        for sub in sync_subs + async_subs:
            if not sub.active:
                continue
            future = self._executor.submit(self._safe_async_call, sub.handler, event)
            futures.append(future)

        return futures

    def _safe_async_call(self, handler, event):
        try:
            handler(event)
        except Exception as e:
            self._logger.error(f"Async handler failed: {e}")

    def shutdown(self):
        self._executor.shutdown(wait=True)
