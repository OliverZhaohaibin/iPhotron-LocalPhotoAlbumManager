import logging
from enum import Enum
from typing import Callable, Optional
from src.iPhoto.events.bus import Event, EventBus
from dataclasses import dataclass, field

class ErrorSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass(kw_only=True)
class ErrorOccurredEvent(Event):
    error: Exception
    severity: ErrorSeverity
    context: dict = field(default_factory=dict)

class ErrorHandler:
    def __init__(self, logger: logging.Logger, event_bus: EventBus):
        self._logger = logger
        self._events = event_bus
        self._ui_callback: Optional[Callable[[str, ErrorSeverity], None]] = None

    def register_ui_callback(self, callback: Callable[[str, ErrorSeverity], None]):
        self._ui_callback = callback

    def handle(self, error: Exception, severity: ErrorSeverity = ErrorSeverity.ERROR, context: dict = None):
        # Log the error
        log_method = getattr(self._logger, severity.value, self._logger.error)
        log_method(f"{error.__class__.__name__}: {error}", extra=context or {})

        # Publish event
        self._events.publish(ErrorOccurredEvent(
            error=error,
            severity=severity,
            context=context or {}
        ))

        # Notify UI
        if self._ui_callback and severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL):
            self._ui_callback(str(error), severity)
