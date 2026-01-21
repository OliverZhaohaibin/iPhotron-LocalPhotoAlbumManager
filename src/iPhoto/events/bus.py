import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Type
from concurrent.futures import ThreadPoolExecutor

@dataclass(kw_only=True)
class Event:
    """Base event class."""
    timestamp: datetime = field(default_factory=datetime.now)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

class EventBus:
    def __init__(self, logger: logging.Logger = None):
        self._logger = logger or logging.getLogger(__name__)
        self._sync_handlers: Dict[Type[Event], List[Callable]] = defaultdict(list)
        self._async_handlers: Dict[Type[Event], List[Callable]] = defaultdict(list)
        self._executor = ThreadPoolExecutor(max_workers=4)

    def subscribe(self, event_type: Type[Event], handler: Callable, async_: bool = False):
        if async_:
            self._async_handlers[event_type].append(handler)
        else:
            self._sync_handlers[event_type].append(handler)

    def publish(self, event: Event):
        event_type = type(event)

        # Synchronous handlers
        for handler in self._sync_handlers[event_type]:
            try:
                handler(event)
            except Exception as e:
                self._logger.error(f"Sync handler failed for {event_type.__name__}: {e}")

        # Asynchronous handlers
        for handler in self._async_handlers[event_type]:
            self._executor.submit(self._safe_async_call, handler, event)

    def _safe_async_call(self, handler, event):
        try:
            handler(event)
        except Exception as e:
            self._logger.error(f"Async handler failed: {e}")

    def shutdown(self):
        self._executor.shutdown(wait=True)
