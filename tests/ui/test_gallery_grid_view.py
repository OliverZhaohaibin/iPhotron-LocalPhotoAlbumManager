"""Unit tests for GalleryQuickWidget theme functionality."""

from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.ui.widgets.gallery_grid_view import GalleryQuickWidget
from src.iPhoto.gui.ui.models.roles import Roles

# Attempt to patch load_icon in asset_delegate if it exists
def patch_delegate_icons(monkeypatch):
    # AssetGridDelegate doesn't use load_icon anymore, so this patch is likely obsolete.
    # We'll wrap it in try-except to avoid breaking tests if the import path is invalid.
    try:
        from PySide6.QtGui import QIcon
        def mock_load_icon(*args, **kwargs):
            return QIcon()

        # Patch where it is used. AssetGridDelegate imports it as `from ..icons import load_icon`
        monkeypatch.setattr("src.iPhoto.gui.ui.widgets.asset_delegate.load_icon", mock_load_icon)
    except (ImportError, AttributeError) as e:
        print(f"patch_delegate_icons: Could not patch load_icon: {e}")


@pytest.fixture(scope="module")
def qapp_instance():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def gallery_widget(qapp_instance):
    """Create a GalleryQuickWidget for testing."""
    widget = GalleryQuickWidget()
    yield widget
    widget.deleteLater()


def test_gallery_quick_widget_initialization(gallery_widget):
    """Test that GalleryQuickWidget initializes correctly."""
    assert gallery_widget._model is None
    assert gallery_widget._theme_colors is None
    assert gallery_widget._selection_mode_enabled is False
    assert gallery_widget._preview_enabled is True
    assert gallery_widget._external_drop_enabled is False


def test_gallery_quick_widget_selection_mode(gallery_widget):
    """Test that selection mode can be toggled."""
    # Initially disabled
    assert gallery_widget.selection_mode_active() is False

    # Enable selection mode
    gallery_widget.set_selection_mode_enabled(True)
    assert gallery_widget.selection_mode_active() is True

    # Disable selection mode
    gallery_widget.set_selection_mode_enabled(False)
    assert gallery_widget.selection_mode_active() is False


def test_gallery_quick_widget_preview_enabled(gallery_widget):
    """Test that preview can be enabled/disabled."""
    # Initially enabled
    assert gallery_widget.preview_enabled() is True

    # Disable preview
    gallery_widget.set_preview_enabled(False)
    assert gallery_widget.preview_enabled() is False

    # Enable preview
    gallery_widget.set_preview_enabled(True)
    assert gallery_widget.preview_enabled() is True


def test_gallery_quick_widget_configure_external_drop(gallery_widget):
    """Test external drop configuration."""
    # Initially disabled
    assert gallery_widget._external_drop_enabled is False
    assert gallery_widget._drop_handler is None
    assert gallery_widget._drop_validator is None

    # Configure with handler
    handler = MagicMock()
    validator = MagicMock()
    gallery_widget.configure_external_drop(handler=handler, validator=validator)

    assert gallery_widget._external_drop_enabled is True
    assert gallery_widget._drop_handler is handler
    assert gallery_widget._drop_validator is validator

    # Disable by setting handler to None
    gallery_widget.configure_external_drop(handler=None)
    assert gallery_widget._external_drop_enabled is False


def test_gallery_quick_widget_set_model(qapp_instance, gallery_widget):
    """Test setting a model on the gallery widget."""
    model = QStandardItemModel()
    for i in range(3):
        item = QStandardItem()
        item.setData(f"item_{i}", Qt.DisplayRole)
        model.appendRow(item)

    gallery_widget.setModel(model)
    assert gallery_widget.model() is model


def test_gallery_quick_widget_apply_background_color(gallery_widget):
    """Test that _apply_background_color correctly sets palette colors."""
    test_color = QColor("#123456")
    gallery_widget._apply_background_color(test_color)

    palette = gallery_widget.palette()
    assert palette.color(QPalette.ColorRole.Window) == test_color
    assert palette.color(QPalette.ColorRole.Base) == test_color


def test_gallery_quick_widget_apply_background_color_enables_autofill(gallery_widget):
    """Test that _apply_background_color enables auto fill background."""
    test_color = QColor("#123456")
    gallery_widget._apply_background_color(test_color)

    assert gallery_widget.autoFillBackground() is True


def test_gallery_quick_widget_viewport(gallery_widget):
    """Test that viewport() returns self for compatibility."""
    assert gallery_widget.viewport() is gallery_widget


def test_gallery_quick_widget_selection_model(gallery_widget):
    """Test that selectionModel() returns None (selection is QML-managed)."""
    assert gallery_widget.selectionModel() is None


def test_gallery_quick_widget_item_delegate(gallery_widget):
    """Test that setItemDelegate is a no-op and itemDelegate returns None."""
    gallery_widget.setItemDelegate(MagicMock())
    assert gallery_widget.itemDelegate() is None
