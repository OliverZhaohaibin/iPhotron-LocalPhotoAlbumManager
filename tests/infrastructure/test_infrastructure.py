import pytest
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import Mock, MagicMock

from iPhoto.di.container import DependencyContainer
from iPhoto.events.bus import EventBus, Event
from iPhoto.infrastructure.db.pool import ConnectionPool
from iPhoto.errors.handler import ErrorHandler, ErrorSeverity, ErrorOccurredEvent

# --- DependencyContainer Tests ---

class IService:
    pass

class ServiceImpl(IService):
    def __init__(self, value=None):
        self.value = value

def test_container_register_resolve_singleton():
    container = DependencyContainer()
    container.register_singleton(IService, ServiceImpl)

    instance1 = container.resolve(IService)
    instance2 = container.resolve(IService)

    assert isinstance(instance1, ServiceImpl)
    assert instance1 is instance2

def test_container_register_resolve_transient():
    container = DependencyContainer()
    container.register_transient(IService, ServiceImpl)

    instance1 = container.resolve(IService)
    instance2 = container.resolve(IService)

    assert isinstance(instance1, ServiceImpl)
    assert instance1 is not instance2

def test_container_factory():
    container = DependencyContainer()
    container.register_factory(IService, lambda: ServiceImpl(value="test"))

    instance = container.resolve(IService)
    assert instance.value == "test"

# --- EventBus Tests ---

from dataclasses import dataclass

@dataclass(kw_only=True)
class TestEvent(Event):
    data: str

def test_event_bus_sync_subscription():
    bus = EventBus()
    received = []

    def handler(event: TestEvent):
        received.append(event.data)

    bus.subscribe(TestEvent, handler)
    bus.publish(TestEvent(data="hello"))

    assert received == ["hello"]

def test_event_bus_async_subscription():
    bus = EventBus()
    received = []
    event = threading.Event()

    def handler(e: TestEvent):
        received.append(e.data)
        event.set()

    bus.subscribe(TestEvent, handler, async_=True)
    bus.publish(TestEvent(data="async"))

    assert event.wait(timeout=1.0)
    assert received == ["async"]
    bus.shutdown()

# --- ConnectionPool Tests ---

def test_connection_pool(tmp_path):
    db_path = tmp_path / "test.db"
    pool = ConnectionPool(db_path, pool_size=2)

    # Initialize DB
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")

    # Test connection context
    with pool.connection() as conn:
        conn.execute("INSERT INTO test (value) VALUES (?)", ("foo",))

    with pool.connection() as conn:
        row = conn.execute("SELECT value FROM test").fetchone()
        assert row["value"] == "foo"

    pool.close_all()

def test_connection_pool_concurrency(tmp_path):
    db_path = tmp_path / "test_concurrent.db"
    pool = ConnectionPool(db_path, pool_size=2)

    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")

    def worker(i):
        with pool.connection() as conn:
            conn.execute("INSERT INTO test (value) VALUES (?)", (f"val{i}",))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with pool.connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM test").fetchone()[0]
        assert count == 10

    pool.close_all()

# --- ErrorHandler Tests ---

def test_error_handler():
    logger = Mock()
    bus = EventBus()
    handler = ErrorHandler(logger, bus)

    received_events = []
    bus.subscribe(ErrorOccurredEvent, lambda e: received_events.append(e))

    ui_callback = Mock()
    handler.register_ui_callback(ui_callback)

    error = ValueError("oops")
    handler.handle(error, ErrorSeverity.ERROR, {"context": "test"})

    # Verify logger called
    logger.error.assert_called()

    # Verify event published
    assert len(received_events) == 1
    assert received_events[0].error == error

    # Verify UI callback
    ui_callback.assert_called_with("oops", ErrorSeverity.ERROR)
