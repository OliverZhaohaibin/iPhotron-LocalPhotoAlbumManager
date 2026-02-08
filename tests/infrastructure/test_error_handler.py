import pytest
import logging
from unittest.mock import Mock, MagicMock
from src.iPhoto.errors.handler import ErrorHandler, ErrorSeverity, ErrorOccurredEvent
from src.iPhoto.events.bus import EventBus

def test_handle_error_logs_and_publishes():
    logger = Mock(spec=logging.Logger)
    event_bus = Mock(spec=EventBus)
    handler = ErrorHandler(logger, event_bus)

    error = ValueError("test error")
    handler.handle(error, ErrorSeverity.ERROR)

    # Check logging
    logger.error.assert_called()

    # Check event publishing
    event_bus.publish.assert_called()
    args = event_bus.publish.call_args[0]
    event = args[0]
    assert isinstance(event, ErrorOccurredEvent)
    assert event.error == error
    assert event.severity == ErrorSeverity.ERROR

def test_ui_callback():
    logger = Mock(spec=logging.Logger)
    event_bus = Mock(spec=EventBus)
    handler = ErrorHandler(logger, event_bus)

    callback = Mock()
    handler.register_ui_callback(callback)

    error = RuntimeError("ui error")
    handler.handle(error, ErrorSeverity.CRITICAL)

    callback.assert_called_with("ui error", ErrorSeverity.CRITICAL)

def test_ignore_info_severity_in_ui():
    logger = Mock(spec=logging.Logger)
    event_bus = Mock(spec=EventBus)
    handler = ErrorHandler(logger, event_bus)

    callback = Mock()
    handler.register_ui_callback(callback)

    error = Exception("info")
    handler.handle(error, ErrorSeverity.INFO)

    callback.assert_not_called()
