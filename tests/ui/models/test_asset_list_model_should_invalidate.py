
import pytest
from unittest.mock import MagicMock
from src.iPhoto.gui.ui.models.asset_list_model import AssetListModel

class TestAssetListModelShouldInvalidate:
    @pytest.fixture
    def model(self):
        # Mock dependencies to instantiate the model
        facade = MagicMock()
        # Mock QTimer and QMutex if they are instantiated in __init__
        with pytest.MonkeyPatch.context() as m:
            m.setattr("PySide6.QtCore.QTimer", MagicMock())
            m.setattr("PySide6.QtCore.QThreadPool", MagicMock())
            m.setattr("src.iPhoto.gui.ui.models.asset_list_model.AssetCacheManager", MagicMock())
            m.setattr("src.iPhoto.gui.ui.models.asset_list_model.AssetDataLoader", MagicMock())
            m.setattr("src.iPhoto.gui.ui.models.asset_list_model.AssetListStateManager", MagicMock())
            m.setattr("src.iPhoto.gui.ui.models.asset_list_model.AssetRowAdapter", MagicMock())

            model = AssetListModel(facade)
        return model

    def test_invalidation_triggers_on_visual_fields(self, model):
        """Verify that changes to visual fields trigger invalidation."""
        base_row = {"rel": "photo.jpg", "ts": 100, "bytes": 500, "abs": "/tmp/a", "w": 100, "h": 100}

        # Timestamp change
        assert model._should_invalidate_thumbnail(base_row, {**base_row, "ts": 101})

        # Size change
        assert model._should_invalidate_thumbnail(base_row, {**base_row, "bytes": 501})

        # Absolute path change
        assert model._should_invalidate_thumbnail(base_row, {**base_row, "abs": "/tmp/b"})

        # Width/Height change
        assert model._should_invalidate_thumbnail(base_row, {**base_row, "w": 200})
        assert model._should_invalidate_thumbnail(base_row, {**base_row, "h": 200})

        # Still image time change
        assert model._should_invalidate_thumbnail(base_row, {**base_row, "still_image_time": 1.5})

    def test_invalidation_skips_on_non_visual_fields(self, model):
        """Verify that changes to non-visual fields do NOT trigger invalidation."""
        base_row = {"rel": "photo.jpg", "ts": 100, "bytes": 500, "abs": "/tmp/a"}

        # Favorite status
        assert not model._should_invalidate_thumbnail(base_row, {**base_row, "is_favorite": True})

        # Live role
        assert not model._should_invalidate_thumbnail(base_row, {**base_row, "live_role": 1})

        # Location
        assert not model._should_invalidate_thumbnail(base_row, {**base_row, "location": "Paris"})

        # GPS
        assert not model._should_invalidate_thumbnail(base_row, {**base_row, "gps": "coords"})

        # Year/Month
        assert not model._should_invalidate_thumbnail(base_row, {**base_row, "year": 2023})
        assert not model._should_invalidate_thumbnail(base_row, {**base_row, "month": 10})

    def test_edge_cases_missing_fields(self, model):
        """Verify behavior when fields are missing or None."""
        row_full = {"ts": 100, "bytes": 500}
        row_missing = {"ts": 100} # missing 'bytes'

        # Missing key vs Present key (effectively changed from None to 500)
        # old_row.get("bytes") -> None, new_row.get("bytes") -> 500. Should invalidate.
        assert model._should_invalidate_thumbnail(row_missing, row_full)

        # Present key vs Missing key
        assert model._should_invalidate_thumbnail(row_full, row_missing)

        # Both missing (None == None) -> False
        row_a = {"ts": 100}
        row_b = {"ts": 100}
        assert not model._should_invalidate_thumbnail(row_a, row_b)

    def test_mixed_changes(self, model):
        """Verify that if at least one visual field changes, it returns True, even if non-visuals also change."""
        base_row = {"rel": "photo.jpg", "ts": 100, "is_favorite": False}
        new_row = {"rel": "photo.jpg", "ts": 101, "is_favorite": True}

        # ts changed (True) OR is_favorite changed (False) -> True
        assert model._should_invalidate_thumbnail(base_row, new_row)
