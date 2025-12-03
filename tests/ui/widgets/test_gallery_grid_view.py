
import pytest
import math

pytest.importorskip("PySide6")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication
from src.iPhoto.gui.ui.widgets.gallery_grid_view import GalleryGridView

@pytest.fixture(scope="module")
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_gallery_grid_initial_properties(qapp):
    grid = GalleryGridView()
    assert grid.spacing() == 4
    # Ensure setGridSize was effectively removed or ignored (by checking it's not enforcing a fixed size logic,
    # but technically QListView keeps the property if set, but we removed the call.
    # Default grid size is usually empty.
    assert grid.gridSize().isEmpty()

def test_gallery_grid_resize_behavior(qapp):
    grid = GalleryGridView()
    # Resize to a known size. Note: viewport width will be slightly less due to borders usually.
    # We use a large enough size to have > 1 columns.
    grid.resize(800, 600)
    grid.show()
    qapp.processEvents()

    viewport_width = grid.viewport().width()
    # Verify we have a valid viewport
    assert viewport_width > 0

    gap = 4
    min_item_width = 192

    # Expected Logic
    available_width = viewport_width - gap
    item_footprint = min_item_width + gap
    n_columns = math.floor(available_width / item_footprint)
    if n_columns <= 0:
        n_columns = 1

    expected_w = int((viewport_width - (n_columns + 1) * gap) / n_columns)

    current_size = grid.iconSize()
    assert current_size.width() == expected_w
    assert current_size.height() == expected_w
    # Grid size should be icon size + gap (4px) to ensure correct spacing stride
    assert grid.gridSize().width() == expected_w + gap
    assert grid.gridSize().height() == expected_w + gap

    # Test resizing to a smaller width
    grid.resize(300, 600)
    qapp.processEvents()

    viewport_width = grid.viewport().width()
    available_width = viewport_width - gap
    n_columns = math.floor(available_width / item_footprint)
    if n_columns <= 0:
        n_columns = 1
    expected_w = int((viewport_width - (n_columns + 1) * gap) / n_columns)

    assert grid.iconSize().width() == expected_w
    assert grid.gridSize().width() == expected_w + gap
