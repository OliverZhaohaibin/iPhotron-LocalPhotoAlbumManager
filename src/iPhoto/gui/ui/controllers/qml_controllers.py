"""QML Controllers for bridging Python backend to QML frontend.

This module provides QObject-based controllers that expose application logic
to the QML interface via Qt's property, signal, and slot system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, Signal, Slot

from ....settings import SettingsManager

if TYPE_CHECKING:
    from ..models.album_tree_model import AlbumTreeModel
    from ..models.asset_model import AssetListModel


DEFAULT_THEME_MODE = "system"


class ThemeController(QObject):
    """Controller for managing application theme settings."""

    modeChanged = Signal(str)

    def __init__(
        self,
        settings: SettingsManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        valid_modes = ("light", "dark", DEFAULT_THEME_MODE)
        stored = (
            settings.get("ui.theme", DEFAULT_THEME_MODE)
            if settings is not None
            else DEFAULT_THEME_MODE
        )
        self._mode = stored if stored in valid_modes else DEFAULT_THEME_MODE

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        """Current theme mode: 'light', 'dark', or 'system'."""
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if self._mode != value and value in ("light", "dark", DEFAULT_THEME_MODE):
            self._mode = value
            if self._settings is not None:
                self._settings.set("ui.theme", value)
            self.modeChanged.emit(value)

    @Slot(str)
    def setMode(self, mode: str) -> None:
        """Set the theme mode from QML."""
        self.mode = mode


class AlbumController(QObject):
    """Controller for album navigation and selection."""

    modelChanged = Signal()
    selectionChanged = Signal(str)
    allPhotosSelected = Signal()

    def __init__(
        self,
        model: "AlbumTreeModel",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = model
        self._current_selection: str | None = None
        self._current_static: str | None = None

    @Property(QObject, notify=modelChanged)
    def model(self) -> "AlbumTreeModel":
        """The album tree model for QML binding."""
        return self._model

    @Property(str, notify=selectionChanged)
    def currentAlbum(self) -> str:
        """Path of the currently selected album."""
        return self._current_selection or ""

    @Slot(str)
    def selectAlbum(self, path: str) -> None:
        """Select an album by path."""
        if self._current_selection != path:
            self._current_selection = path
            self._current_static = None
            self.selectionChanged.emit(path)

    @Slot()
    def selectAllPhotos(self) -> None:
        """Select the All Photos view."""
        self._current_selection = None
        self._current_static = "All Photos"
        self.allPhotosSelected.emit()


class AssetController(QObject):
    """Controller for asset list management."""

    modelChanged = Signal()
    totalCountChanged = Signal(int)
    selectionChanged = Signal()

    def __init__(
        self,
        model: "AssetListModel",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = model
        self._selected_indices: list[int] = []

        # Connect model signals
        self._model.rowsInserted.connect(self._on_model_changed)
        self._model.rowsRemoved.connect(self._on_model_changed)
        self._model.modelReset.connect(self._on_model_changed)

    def _on_model_changed(self) -> None:
        """Handle model changes."""
        self.totalCountChanged.emit(self._model.rowCount())

    @Property(QObject, notify=modelChanged)
    def model(self) -> "AssetListModel":
        """The asset list model for QML binding."""
        return self._model

    @Slot(QObject)
    def setModel(self, model: "AssetListModel") -> None:
        """Replace the backing model when the active album changes."""

        if model is None or model is self._model:
            return
        try:
            self._model.rowsInserted.disconnect(self._on_model_changed)
            self._model.rowsRemoved.disconnect(self._on_model_changed)
            self._model.modelReset.disconnect(self._on_model_changed)
        except (RuntimeError, TypeError):
            # Safe to ignore if connections were already cleared or model is gone
            pass
        self._model = model
        self._model.rowsInserted.connect(self._on_model_changed)
        self._model.rowsRemoved.connect(self._on_model_changed)
        self._model.modelReset.connect(self._on_model_changed)
        self.modelChanged.emit()
        self._on_model_changed()

    @Property(int, notify=totalCountChanged)
    def totalCount(self) -> int:
        """Total number of assets in the model."""
        return self._model.rowCount()

    @Property(list, notify=selectionChanged)
    def selectedIndices(self) -> list[int]:
        """List of selected asset indices."""
        return self._selected_indices

    @Slot(int)
    def selectAsset(self, index: int) -> None:
        """Select a single asset."""
        self._selected_indices = [index]
        self.selectionChanged.emit()

    @Slot(int)
    def toggleSelection(self, index: int) -> None:
        """Toggle selection state of an asset."""
        if index in self._selected_indices:
            self._selected_indices.remove(index)
        else:
            self._selected_indices.append(index)
        self.selectionChanged.emit()

    @Slot()
    def clearSelection(self) -> None:
        """Clear all selections."""
        self._selected_indices.clear()
        self.selectionChanged.emit()

    @Slot(int, int)
    def prioritizeRows(self, first: int, last: int) -> None:
        """Hint the model to prioritise thumbnail loading for visible rows."""

        if hasattr(self._model, "prioritize_rows"):
            self._model.prioritize_rows(first, last)  # type: ignore[attr-defined]

    @Slot(int)
    def openDetail(self, index: int) -> None:  # noqa: N802 - Qt slot naming
        """Open the detail view for an asset.

        TODO: Implement navigation to detail view. This is a placeholder
        that will be connected to the view controller when the full
        QML migration is complete.
        """


class StatusController(QObject):
    """Controller for status bar updates."""

    messageChanged = Signal(str, int)  # message, timeout
    progressChanged = Signal(float)  # -1 for indeterminate, 0-100 for progress

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._message = ""

    @Property(str, notify=messageChanged)
    def message(self) -> str:
        """Current status message."""
        return self._message

    @Slot(str, int)
    def showMessage(self, message: str, timeout: int = 0) -> None:
        """Show a status message."""
        self._message = message
        self.messageChanged.emit(message, timeout)

    @Slot()
    def clearMessage(self) -> None:
        """Clear the current message."""
        self._message = ""
        self.messageChanged.emit("", 0)

    @Slot(float)
    def showProgress(self, value: float) -> None:
        """Update progress indicator. -1 for indeterminate."""
        self.progressChanged.emit(value)

    @Slot()
    def hideProgress(self) -> None:
        """Hide the progress indicator."""
        self.progressChanged.emit(100.0)


class EditSessionController(QObject):
    """Controller for edit session state management.

    This controller exposes edit parameters to QML and handles
    bidirectional binding with the underlying EditSession model.
    """

    # Light adjustments
    brillianceChanged = Signal(float)
    exposureChanged = Signal(float)
    highlightsChanged = Signal(float)
    shadowsChanged = Signal(float)
    contrastChanged = Signal(float)
    brightnessChanged = Signal(float)
    blackPointChanged = Signal(float)

    # Color adjustments
    saturationChanged = Signal(float)
    vibranceChanged = Signal(float)
    warmthChanged = Signal(float)
    tintChanged = Signal(float)

    # B&W adjustments
    intensityChanged = Signal(float)
    neutralsChanged = Signal(float)
    toneChanged = Signal(float)
    grainChanged = Signal(float)

    # Session state
    hasChangesChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._brilliance = 0.0
        self._exposure = 0.0
        self._highlights = 0.0
        self._shadows = 0.0
        self._contrast = 0.0
        self._brightness = 0.0
        self._black_point = 0.0

        self._saturation = 0.0
        self._vibrance = 0.0
        self._warmth = 0.0
        self._tint = 0.0

        self._intensity = 0.0
        self._neutrals = 0.0
        self._tone = 0.0
        self._grain = 0.0

    # Light properties
    @Property(float, notify=brillianceChanged)
    def brilliance(self) -> float:
        return self._brilliance

    @brilliance.setter
    def brilliance(self, value: float) -> None:
        if self._brilliance != value:
            self._brilliance = value
            self.brillianceChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=exposureChanged)
    def exposure(self) -> float:
        return self._exposure

    @exposure.setter
    def exposure(self, value: float) -> None:
        if self._exposure != value:
            self._exposure = value
            self.exposureChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=highlightsChanged)
    def highlights(self) -> float:
        return self._highlights

    @highlights.setter
    def highlights(self, value: float) -> None:
        if self._highlights != value:
            self._highlights = value
            self.highlightsChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=shadowsChanged)
    def shadows(self) -> float:
        return self._shadows

    @shadows.setter
    def shadows(self, value: float) -> None:
        if self._shadows != value:
            self._shadows = value
            self.shadowsChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=contrastChanged)
    def contrast(self) -> float:
        return self._contrast

    @contrast.setter
    def contrast(self, value: float) -> None:
        if self._contrast != value:
            self._contrast = value
            self.contrastChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=brightnessChanged)
    def brightness(self) -> float:
        return self._brightness

    @brightness.setter
    def brightness(self, value: float) -> None:
        if self._brightness != value:
            self._brightness = value
            self.brightnessChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=blackPointChanged)
    def blackPoint(self) -> float:
        return self._black_point

    @blackPoint.setter
    def blackPoint(self, value: float) -> None:
        if self._black_point != value:
            self._black_point = value
            self.blackPointChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    # Color properties
    @Property(float, notify=saturationChanged)
    def saturation(self) -> float:
        return self._saturation

    @saturation.setter
    def saturation(self, value: float) -> None:
        if self._saturation != value:
            self._saturation = value
            self.saturationChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=vibranceChanged)
    def vibrance(self) -> float:
        return self._vibrance

    @vibrance.setter
    def vibrance(self, value: float) -> None:
        if self._vibrance != value:
            self._vibrance = value
            self.vibranceChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=warmthChanged)
    def warmth(self) -> float:
        return self._warmth

    @warmth.setter
    def warmth(self, value: float) -> None:
        if self._warmth != value:
            self._warmth = value
            self.warmthChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=tintChanged)
    def tint(self) -> float:
        return self._tint

    @tint.setter
    def tint(self, value: float) -> None:
        if self._tint != value:
            self._tint = value
            self.tintChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    # B&W properties
    @Property(float, notify=intensityChanged)
    def intensity(self) -> float:
        return self._intensity

    @intensity.setter
    def intensity(self, value: float) -> None:
        if self._intensity != value:
            self._intensity = value
            self.intensityChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=neutralsChanged)
    def neutrals(self) -> float:
        return self._neutrals

    @neutrals.setter
    def neutrals(self, value: float) -> None:
        if self._neutrals != value:
            self._neutrals = value
            self.neutralsChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=toneChanged)
    def tone(self) -> float:
        return self._tone

    @tone.setter
    def tone(self, value: float) -> None:
        if self._tone != value:
            self._tone = value
            self.toneChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(float, notify=grainChanged)
    def grain(self) -> float:
        return self._grain

    @grain.setter
    def grain(self, value: float) -> None:
        if self._grain != value:
            self._grain = value
            self.grainChanged.emit(value)
            self.hasChangesChanged.emit(self.hasChanges)

    @Property(bool, notify=hasChangesChanged)
    def hasChanges(self) -> bool:
        """Check if any values differ from defaults."""
        return any([
            self._brilliance != 0,
            self._exposure != 0,
            self._highlights != 0,
            self._shadows != 0,
            self._contrast != 0,
            self._brightness != 0,
            self._black_point != 0,
            self._saturation != 0,
            self._vibrance != 0,
            self._warmth != 0,
            self._tint != 0,
            self._intensity != 0,
            self._neutrals != 0,
            self._tone != 0,
            self._grain != 0,
        ])

    @Slot()
    def resetAll(self) -> None:
        """Reset all values to defaults."""
        self.brilliance = 0.0
        self.exposure = 0.0
        self.highlights = 0.0
        self.shadows = 0.0
        self.contrast = 0.0
        self.brightness = 0.0
        self.blackPoint = 0.0

        self.saturation = 0.0
        self.vibrance = 0.0
        self.warmth = 0.0
        self.tint = 0.0

        self.intensity = 0.0
        self.neutrals = 0.0
        self.tone = 0.0
        self.grain = 0.0


__all__ = [
    "AlbumController",
    "AssetController",
    "EditSessionController",
    "StatusController",
    "ThemeController",
]
