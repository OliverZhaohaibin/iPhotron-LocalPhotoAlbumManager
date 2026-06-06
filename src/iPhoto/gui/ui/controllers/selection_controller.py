"""Controller dedicated to multi-selection mode handling for the gallery grid."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QObject, QCoreApplication, Signal
from PySide6.QtWidgets import QPushButton

from ..models.roles import Roles
from ..widgets.asset_grid import AssetGrid
from ..widgets.asset_delegate import AssetGridDelegate
from .preview_controller import PreviewController


if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...coordinators.playback_coordinator import PlaybackCoordinator


class SelectionController(QObject):
    """Manage the gallery's selection mode separate from the main coordinator."""

    selectionModeChanged = Signal(bool)

    def __init__(
        self,
        selection_button: QPushButton,
        grid_view: AssetGrid,
        grid_delegate: AssetGridDelegate | None,
        preview_controller: PreviewController,
        playback: Optional["PlaybackCoordinator"] = None,
        *,
        handle_grid_clicks: bool = True,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._selection_button = selection_button
        self._grid_view = grid_view
        self._grid_delegate = grid_delegate
        self._preview_controller = preview_controller
        self._playback = playback
        self._active = False
        self._selection_paths: list[Path] = []
        self._bound_model = None
        self._bound_selection_model = None
        self._restoring_selection = False
        self._selection_tracking_suspended = False

        self._selection_button.clicked.connect(self._handle_toggle_requested)
        if handle_grid_clicks and self._playback is not None:
            self._grid_view.itemClicked.connect(self._handle_grid_item_clicked)
        model_about_to_change = getattr(self._grid_view, "modelAboutToChange", None)
        if model_about_to_change is not None:
            model_about_to_change.connect(self._capture_selection_before_reset)
        model_changed = getattr(self._grid_view, "modelChanged", None)
        if model_changed is not None:
            model_changed.connect(self._handle_model_changed)
        self._handle_model_changed(self._grid_view.model())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_selection_mode(self, enabled: bool) -> None:
        """Enable or disable multi-selection mode.

        The method keeps the grid widget, delegate, and toggle button in sync
        so the rest of the UI can simply query :meth:`is_active` and react to
        the change.  When selection mode is disabled the current selection is
        cleared to avoid leaving stale highlights behind.
        """

        desired_state = bool(enabled)
        if self._active == desired_state:
            if not desired_state:
                self._grid_view.clearSelection()
            return

        self._active = desired_state
        self._update_button_state(desired_state)

        self._grid_view.set_selection_mode_enabled(desired_state)
        if self._grid_delegate is not None:
            self._grid_delegate.set_selection_mode_active(desired_state)
        self._selection_paths = []
        self._grid_view.clearSelection()
        if not desired_state:
            self._preview_controller.close_preview(False)

        self.selectionModeChanged.emit(self._active)

    def is_active(self) -> bool:
        """Return ``True`` when multi-selection mode is currently enabled."""

        return self._active

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _update_button_state(self, enabled: bool) -> None:
        if enabled:
            self._selection_button.setText(
                QCoreApplication.translate("MainWindow", "Cancel")
            )
            self._selection_button.setToolTip(
                QCoreApplication.translate("MainWindow", "Exit multi-selection mode")
            )
        else:
            self._selection_button.setText(
                QCoreApplication.translate("MainWindow", "Select")
            )
            self._selection_button.setToolTip(
                QCoreApplication.translate("MainWindow", "Toggle multi-selection mode")
            )

    def _handle_toggle_requested(self) -> None:
        if not self._selection_button.isEnabled():
            return
        self.set_selection_mode(not self._active)

    def _handle_grid_item_clicked(self, index: QModelIndex) -> None:
        if self._active:
            return
        if self._playback is None:
            return
        self._playback.play_asset(index.row())

    def _handle_model_changed(self, model) -> None:
        self._bind_model_reset_signals(model)
        self._bind_selection_model(self._grid_view.selectionModel())
        self._restore_selection_after_model_change()

    def _bind_model_reset_signals(self, model) -> None:
        if model is self._bound_model:
            return
        if self._bound_model is not None:
            try:
                self._bound_model.modelAboutToBeReset.disconnect(
                    self._capture_selection_before_reset
                )
            except (RuntimeError, TypeError):
                pass
            try:
                self._bound_model.modelReset.disconnect(
                    self._restore_selection_after_model_change
                )
            except (RuntimeError, TypeError):
                pass
            try:
                self._bound_model.dataChanged.disconnect(
                    self._restore_selection_after_model_change
                )
            except (RuntimeError, TypeError):
                pass
            try:
                self._bound_model.rowsInserted.disconnect(
                    self._restore_selection_after_model_change
                )
            except (RuntimeError, TypeError):
                pass
            try:
                self._bound_model.rowsRemoved.disconnect(
                    self._restore_selection_after_model_change
                )
            except (RuntimeError, TypeError):
                pass
        self._bound_model = model
        if model is not None:
            model.modelAboutToBeReset.connect(self._capture_selection_before_reset)
            model.modelReset.connect(self._restore_selection_after_model_change)
            model.dataChanged.connect(self._restore_selection_after_model_change)
            model.rowsInserted.connect(self._restore_selection_after_model_change)
            model.rowsRemoved.connect(self._restore_selection_after_model_change)

    def _bind_selection_model(self, selection_model) -> None:
        if selection_model is self._bound_selection_model:
            return
        if self._bound_selection_model is not None:
            try:
                self._bound_selection_model.selectionChanged.disconnect(
                    self._remember_current_selection
                )
            except (RuntimeError, TypeError):
                pass
        self._bound_selection_model = selection_model
        if selection_model is not None:
            selection_model.selectionChanged.connect(self._remember_current_selection)

    def _capture_selection_before_reset(self) -> None:
        self._remember_current_selection()
        if self._active:
            self._selection_tracking_suspended = True

    def _remember_current_selection(self, *_args) -> None:
        if (
            not self._active
            or self._restoring_selection
            or self._selection_tracking_suspended
        ):
            return
        self._selection_paths = self._current_selection_paths()

    def _current_selection_paths(self) -> list[Path]:
        selection_model = self._grid_view.selectionModel()
        if selection_model is None:
            return []
        seen: set[Path] = set()
        paths: list[Path] = []
        for index in selection_model.selectedIndexes():
            if not index.isValid():
                continue
            raw_path = index.data(Roles.ABS)
            if not raw_path:
                continue
            path = Path(str(raw_path))
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def _restore_selection_after_model_change(self, *_args) -> None:
        if not self._active:
            self._selection_tracking_suspended = False
            return
        if not self._selection_paths:
            self._selection_tracking_suspended = False
            return
        model = self._grid_view.model()
        selection_model = self._grid_view.selectionModel()
        if model is None or selection_model is None:
            self._selection_tracking_suspended = False
            return
        row_for_path = getattr(model, "row_for_path", None)
        if not callable(row_for_path):
            self._selection_tracking_suspended = False
            return

        restored_paths: list[Path] = []
        self._restoring_selection = True
        try:
            selection_model.clearSelection()
            for path in self._selection_paths:
                row = row_for_path(path)
                if row is None or row < 0:
                    continue
                index = model.index(row, 0)
                if not index.isValid():
                    continue
                selection_model.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                restored_paths.append(path)
        finally:
            self._restoring_selection = False
            self._selection_tracking_suspended = False
        self._selection_paths = restored_paths
