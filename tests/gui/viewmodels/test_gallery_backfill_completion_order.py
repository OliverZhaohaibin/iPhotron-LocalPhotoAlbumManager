from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService


def test_same_root_backfill_completions_are_not_deduplicated(qapp) -> None:
    store = MagicMock(spec=GalleryCollectionStore)
    store.data_changed = MagicMock()
    store.window_changed = MagicMock()
    store.row_changed = MagicMock()
    store.count.return_value = 0
    store.asset_query_service = None
    store.record_scan_batch.return_value = True

    thumbnails = MagicMock(spec=ThumbnailCacheService)
    adapter = GalleryListModelAdapter(store, thumbnails)
    adapter._scan_batch_timer.setInterval(60_000)

    root = Path("/library")
    first_batch = SimpleNamespace(root=root, rows=[{"rel": "a.jpg"}])
    second_batch = SimpleNamespace(root=root, rows=[{"rel": "b.jpg"}])
    completed: list[Path] = []
    adapter.thumbnailBackfillCompleted.connect(completed.append)

    adapter._enqueue_thumbnail_backfill_completion_on_ui_thread(first_batch)
    adapter._enqueue_thumbnail_backfill_completion_on_ui_thread(second_batch)

    assert completed == []
    assert store.record_scan_batch.call_count == 2

    adapter._flush_pending_scan_batches()

    store.flush_pending_scan_refresh.assert_called_once_with()
    assert completed == [root, root]
