"""Tests for paged AssetListModel fetch behaviour and worker yielding logic."""

from unittest.mock import MagicMock, patch, call
import pytest
from PySide6.QtCore import QThread
from pathlib import Path

from iPhoto.gui.ui.models.asset_list_model import AssetListModel
from iPhoto.gui.ui.tasks.asset_loader_worker import AssetLoaderWorker, LiveIngestWorker, AssetLoaderSignals


class DummyStateManager:
    def __init__(self) -> None:
        self.rows = []
        self.row_lookup = {}

    def row_count(self) -> int:
        return len(self.rows)

    def append_chunk(self, items):
        start = len(self.rows)
        self.rows.extend(items)
        for offset, item in enumerate(items):
            rel = item.get("rel")
            if rel:
                self.row_lookup[str(rel)] = start + offset

    def on_external_row_inserted(self, *_args, **_kwargs):
        return None

    def set_virtual_reload_suppressed(self, *_args, **_kwargs):
        return None

    def set_virtual_move_requires_revisit(self, *_args, **_kwargs):
        return None

    def clear_reload_pending(self):
        return None


class FakeSource:
    def __init__(self, pages):
        self.pages = list(pages)

    def has_more(self):
        return bool(self.pages)

    def fetch_next(self, limit):
        if not self.pages:
            return []
        page = self.pages.pop(0)
        return page[:limit]

    def reset(self):
        return None


class TestAssetListModelPaging:
    @pytest.fixture
    def facade(self):
        facade = MagicMock()
        facade.library_manager = MagicMock()
        facade.library_manager.root.return_value = None
        facade.current_album = MagicMock()
        facade.current_album.manifest = {}
        return facade

    @pytest.fixture
    def model(self, facade):
        model = AssetListModel(facade)
        model._state_manager = DummyStateManager()
        model._album_root = Path("/tmp")
        return model

    def test_fetch_more_uses_page_size(self, model):
        model._data_source = FakeSource([[{"rel": "a"}]])
        model._page_size = 10
        called = {}
        def fake_trigger(limit):
            called["limit"] = limit
        model._trigger_fetch = fake_trigger
        model.fetchMore(None)
        assert called["limit"] == 10

    def test_on_page_loaded_appends_and_signals(self, model):
        model._data_source = FakeSource([[{"rel": "b"}]])
        model._album_root = Path("/tmp/root")
        finished = []
        model.loadFinished.connect(lambda *_: finished.append(True))

        # Simulate source already consuming its page
        model._data_source.pages = []
        model._on_page_loaded([{"rel": "b"}])

        assert model._state_manager.rows[0]["rel"] == "b"
        assert finished  # should finish because source now empty


class TestWorkerYielding:
    @patch("PySide6.QtCore.QThread.currentThread")
    @patch("PySide6.QtCore.QThread.msleep")
    def test_live_ingest_worker_yielding(self, mock_msleep, mock_current_thread):
        """Test that LiveIngestWorker sets low priority and sleeps periodically."""

        mock_thread = MagicMock()
        mock_current_thread.return_value = mock_thread

        # Create worker with 120 items
        items = [{"rel": f"img{i}.jpg"} for i in range(120)]
        signals = AssetLoaderSignals()
        worker = LiveIngestWorker(MagicMock(), items, [], signals)

        # We need to mock build_asset_entry to return something valid, otherwise it might skip
        with patch("iPhoto.gui.ui.tasks.asset_loader_worker.build_asset_entry") as mock_build:
            mock_build.side_effect = lambda r, row, f, **kwargs: row # just pass through row

            worker.run()

            # Check priority set
            mock_thread.setPriority.assert_called_with(QThread.LowPriority)

            # Check sleep calls.
            # 120 items with enumerate starting at 1, so positions are 1..120
            # Sleep condition: i > 0 and i % 50 == 0
            # Sleeps occur at i=50 and i=100
            # Total 2 sleeps
            assert mock_msleep.call_count == 2
            mock_msleep.assert_has_calls([call(10), call(10)])

    @patch("iPhoto.gui.ui.tasks.asset_loader_worker.PhotoStreamMerger")
    @patch("iPhoto.gui.ui.tasks.asset_loader_worker.QThread")
    @patch("iPhoto.gui.ui.tasks.asset_loader_worker.IndexStore")
    @patch("iPhoto.gui.ui.tasks.asset_loader_worker.ensure_work_dir")
    def test_asset_loader_worker_yielding(self, mock_ensure, MockIndexStore, MockQThread, MockPhotoStreamMerger):
        """Test that AssetLoaderWorker sets low priority and sleeps periodically."""

        # Mock QThread.currentThread().setPriority
        mock_thread_instance = MagicMock()
        MockQThread.currentThread.return_value = mock_thread_instance
        MockQThread.LowPriority = QThread.LowPriority # Preserve constant

        # Mock Store and Generator
        mock_store = MockIndexStore.return_value
        # Mock the context manager __enter__ return value
        mock_store.transaction.return_value.__enter__.return_value = None
        # IMPORTANT: Mock count to return an integer, otherwise comparison fails!
        mock_store.count.return_value = 120

        mock_merger = MockPhotoStreamMerger.return_value
        mock_merger.has_more.side_effect = [True, True, False]
        mock_merger.fetch_next_batch.side_effect = [
            [{"rel": f"img{i}.jpg"} for i in range(60)],
            [{"rel": f"img{i}.jpg"} for i in range(60, 120)],
        ]

        signals = AssetLoaderSignals()
        signals.error = MagicMock()
        signals.finished = MagicMock()

        worker = AssetLoaderWorker(MagicMock(), [], signals)

        # Mock build_asset_entry
        with patch("iPhoto.gui.ui.tasks.asset_loader_worker.build_asset_entry") as mock_build:
            mock_build.side_effect = lambda *args, **kwargs: {"rel": args[1].get("rel", "foo")}

            worker.run()

            # Check for errors
            if signals.error.called:
                pytest.fail(f"Worker emitted error: {signals.error.call_args}")

            # Check priority set
            mock_thread_instance.setPriority.assert_called_with(QThread.LowPriority)

            # Check sleep calls
            # enumerate starts at 1
            # 1..120
            # Sleeps at 50, 100
            assert MockQThread.msleep.call_count == 2
            MockQThread.msleep.assert_has_calls([call(10), call(10)])
