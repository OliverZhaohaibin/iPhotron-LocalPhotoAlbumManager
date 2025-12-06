"""Unit tests for ThumbnailCacheService."""

from __future__ import annotations

from unittest.mock import Mock
from pathlib import Path

import pytest

from src.iPhoto.gui.ui.controllers.thumbnail_cache_service import ThumbnailCacheService


@pytest.fixture
def thumbnail_service():
    asset_model = Mock()
    # Mock source model and its metadata lookup
    source_model = Mock()
    asset_model.source_model.return_value = source_model
    asset_model.thumbnail_loader.return_value = Mock()

    service = ThumbnailCacheService(asset_model)
    return service, asset_model, source_model


def test_schedule_refresh_queues_invalidation(thumbnail_service, qtbot):
    """Verify schedule_refresh queues a refresh for the next loop."""
    service, asset_model, source_model = thumbnail_service
    path = Path("/path/to/image.jpg")
    rel_path = "image.jpg"

    source_model.metadata_for_absolute_path.return_value = {"rel": rel_path}

    service.schedule_refresh(path)
    assert rel_path in service._pending_thumbnail_refreshes

    # Process events to let timer fire
    qtbot.wait(10)

    # After timer fires
    assert rel_path not in service._pending_thumbnail_refreshes
    service._thumbnail_loader.invalidate.assert_called_with(rel_path)
    source_model.invalidate_thumbnail.assert_called_with(rel_path)


def test_schedule_refresh_ignores_duplicates(thumbnail_service, qtbot):
    """Verify multiple calls for same asset don't queue multiple refreshes."""
    service, asset_model, source_model = thumbnail_service
    path = Path("/path/to/image.jpg")
    rel_path = "image.jpg"

    source_model.metadata_for_absolute_path.return_value = {"rel": rel_path}

    service.schedule_refresh(path)
    service.schedule_refresh(path)

    # We rely on set property to avoid duplicates, and checking pending set
    assert len(service._pending_thumbnail_refreshes) == 1
    assert rel_path in service._pending_thumbnail_refreshes


def test_schedule_refresh_no_metadata(thumbnail_service):
    """Verify nothing happens if metadata is missing."""
    service, asset_model, source_model = thumbnail_service
    path = Path("/path/to/image.jpg")

    source_model.metadata_for_absolute_path.return_value = None

    service.schedule_refresh(path)

    assert len(service._pending_thumbnail_refreshes) == 0
    service._thumbnail_loader.invalidate.assert_not_called()
