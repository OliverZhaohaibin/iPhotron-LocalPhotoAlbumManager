"""Tests for AssetListModel backpressure and worker yielding logic."""

from unittest.mock import MagicMock, patch, call
import pytest
from PySide6.QtCore import QThread

from iPhoto.gui.ui.models.asset_list_model import AssetListModel
from iPhoto.gui.ui.tasks.asset_loader_worker import AssetLoaderWorker, LiveIngestWorker, AssetLoaderSignals

class TestAssetListModelBackpressure:
    @pytest.fixture
    def facade(self):
        facade = MagicMock()
        facade.library_manager = MagicMock()
        facade.library_manager.root.return_value = None
        return facade

    @pytest.fixture
    def model(self, facade):
        model = AssetListModel(facade)
        # Mock state manager to avoid complex internal logic during simple buffer tests
        model._state_manager = MagicMock()
        model._state_manager.row_count.return_value = 0
        model._state_manager.append_chunk = MagicMock()
        model._state_manager.on_external_row_inserted = MagicMock()

        # Ensure timer is mocked or controllable?
        # Actually QTimer works in tests if using qtbot or app loop, but here we can inspect calls.
        # We will check if timer is started.
        model._flush_timer = MagicMock()
        
        # Initialize _pending_rels and _pending_abs sets to match runtime behavior
        model._pending_rels = set()
        model._pending_abs = set()

        return model

    def test_backpressure_batching(self, model):
        """Test that chunks are buffered and processed in batches."""

        # Verify tuned constants
        assert model._STREAM_BATCH_SIZE == 100
        assert model._STREAM_FLUSH_THRESHOLD == 2000
        assert model._STREAM_FLUSH_INTERVAL_MS == 100

        # Simulate receiving a large chunk (e.g., 500 items)
        chunk = [{"rel": f"img{i}.jpg"} for i in range(500)]
        model._pending_chunks_buffer = chunk
        model._is_flushing = False

        # Call flush
        model._flush_pending_chunks()

        # 1. Should have consumed 100 items (Batch Size)
        # Remaining: 400
        assert len(model._pending_chunks_buffer) == 400
        model._state_manager.append_chunk.assert_called_once()
        args, _ = model._state_manager.append_chunk.call_args
        assert len(args[0]) == 100
        assert args[0][0]["rel"] == "img0.jpg"
        assert args[0][99]["rel"] == "img99.jpg"

        # 2. Should have started timer for next batch
        model._flush_timer.start.assert_called_once_with(model._STREAM_FLUSH_INTERVAL_MS)

        # Reset mocks
        model._state_manager.append_chunk.reset_mock()
        model._flush_timer.start.reset_mock()

        # Call flush again
        model._flush_pending_chunks()

        # 3. Should have consumed next 100 items
        assert len(model._pending_chunks_buffer) == 300
        model._state_manager.append_chunk.assert_called_once()
        args, _ = model._state_manager.append_chunk.call_args
        assert len(args[0]) == 100
        assert args[0][0]["rel"] == "img100.jpg"

        # 4. Timer started again
        model._flush_timer.start.assert_called_once_with(model._STREAM_FLUSH_INTERVAL_MS)

    def test_buffer_exhaustion(self, model):
        """Test that timer stops when buffer is empty."""

        # Case: 100 items in buffer (exactly one batch)
        chunk = [{"rel": f"img{i}.jpg"} for i in range(100)]
        model._pending_chunks_buffer = chunk

        model._flush_pending_chunks()

        # Buffer empty
        assert len(model._pending_chunks_buffer) == 0

        # Insert happened
        model._state_manager.append_chunk.assert_called_once()

        # Timer should be stopped
        model._flush_timer.stop.assert_called_once()
        # Should NOT be started
        model._flush_timer.start.assert_not_called()

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
            mock_build.side_effect = lambda r, row, f: row # just pass through row

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

    @patch("iPhoto.gui.ui.tasks.asset_loader_worker.QThread")
    @patch("iPhoto.gui.ui.tasks.asset_loader_worker.IndexStore")
    @patch("iPhoto.gui.ui.tasks.asset_loader_worker.ensure_work_dir")
    def test_asset_loader_worker_yielding(self, mock_ensure, MockIndexStore, MockQThread):
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

        # Flag to check if generator was called
        generator_ran = False

        # Generator yielding 120 items
        def fake_generator(*args, **kwargs):
            nonlocal generator_ran
            generator_ran = True
            for i in range(120):
                yield {"rel": f"img{i}.jpg"}

        mock_store.read_geometry_only.side_effect = fake_generator

        signals = AssetLoaderSignals()
        signals.error = MagicMock()
        signals.finished = MagicMock()

        worker = AssetLoaderWorker(MagicMock(), [], signals)

        # Mock build_asset_entry
        with patch("iPhoto.gui.ui.tasks.asset_loader_worker.build_asset_entry") as mock_build:
            mock_build.return_value = {"rel": "foo"}

            worker.run()

            # Check for errors
            if signals.error.called:
                pytest.fail(f"Worker emitted error: {signals.error.call_args}")

            if not generator_ran:
                pytest.fail("Generator was not called! IndexStore logic skipped?")

            # Check priority set
            mock_thread_instance.setPriority.assert_called_with(QThread.LowPriority)

            # Check sleep calls
            # enumerate starts at 1
            # 1..120
            # Sleeps at 50, 100
            assert MockQThread.msleep.call_count == 2
            MockQThread.msleep.assert_has_calls([call(10), call(10)])
