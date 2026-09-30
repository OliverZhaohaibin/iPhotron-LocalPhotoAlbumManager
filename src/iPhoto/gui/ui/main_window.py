"""Qt widgets composing the main application window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...application.contracts.runtime_entry_contract import RuntimeEntryContract
from .ui_main_window import ChromeStatusBar, FeatureKind, Ui_MainWindow
from .window_manager import FramelessWindowManager

class MainWindow(QMainWindow):
    """Primary window for the desktop experience."""

    firstPainted = Signal()

    def __init__(self, context: RuntimeEntryContract) -> None:
        super().__init__()
        self._first_paint_emitted = False
        self._is_shutting_down = False
        self._shutdown_complete = False
        self._startup_orchestrator = None
        self._startup_retry_callback = None

        self.ui = Ui_MainWindow()

        # ``setupUi`` triggers a handful of ``QEvent`` instances while it
        # constructs child widgets.  Those events fire before we can build the
        # frameless chrome helper, so we predeclare the attribute to avoid
        # ``AttributeError`` during the early lifecycle.
        self.window_manager: FramelessWindowManager | None = None

        self.ui.setupUi(self, context.library)
        if hasattr(self.ui, "window_shell") and hasattr(self.ui, "window_shell_layout"):
            self._create_startup_recovery_panel()
        translation = getattr(context, "translation", None)
        language_changed = getattr(translation, "languageChanged", None)
        if language_changed is not None:
            language_changed.connect(self._schedule_retranslate_ui_tree)

        # ``FramelessWindowManager`` is responsible for every custom chrome
        # behaviour.  The main window therefore remains a thin container that
        # simply forwards lifecycle events to the helper.
        self.window_manager = FramelessWindowManager(self, self.ui)
        self.ui.featureCreated.connect(self._on_feature_created)

        self._coordinator_lifecycle = None
        self._gallery_coordinator = None
        self._detail_coordinator = None

        # Retain the behaviour where clicking the chrome gives the window focus
        # so global shortcuts continue to function when no child widget is
        # active.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def _create_startup_recovery_panel(self) -> None:
        """Create a hidden, non-modal recovery surface for startup failures."""

        panel = QFrame(self.ui.window_shell)
        panel.setObjectName("startupRecoveryPanel")
        panel.setStyleSheet(
            "QFrame#startupRecoveryPanel { background: #fff3cd; color: #3d3300; "
            "border-bottom: 1px solid #e2c96f; }"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 8, 12, 8)
        row = QHBoxLayout()
        self._startup_recovery_label = QLabel(panel)
        self._startup_recovery_label.setWordWrap(True)
        row.addWidget(self._startup_recovery_label, 1)
        retry_button = QPushButton("Retry", panel)
        continue_button = QPushButton("Continue without library", panel)
        details_button = QPushButton("Details", panel)
        row.addWidget(retry_button)
        row.addWidget(continue_button)
        row.addWidget(details_button)
        layout.addLayout(row)
        self._startup_recovery_details = QLabel(panel)
        self._startup_recovery_details.setWordWrap(True)
        self._startup_recovery_details.hide()
        layout.addWidget(self._startup_recovery_details)
        retry_button.clicked.connect(self._retry_startup)
        continue_button.clicked.connect(panel.hide)
        details_button.clicked.connect(
            lambda: self._startup_recovery_details.setVisible(
                not self._startup_recovery_details.isVisible()
            )
        )
        self.ui.window_shell_layout.insertWidget(2, panel)
        panel.hide()
        self._startup_recovery_panel = panel
        self._startup_retry_button = retry_button
        self._startup_continue_button = continue_button

    def set_startup_orchestrator(self, orchestrator: object) -> None:
        self._startup_orchestrator = orchestrator

    def show_startup_recovery(
        self,
        message: str,
        *,
        details: str | None = None,
        retry_callback=None,
        suggested_action: str = "retry",
    ) -> None:
        if not hasattr(self, "_startup_recovery_panel"):
            return
        self._startup_retry_callback = retry_callback
        translated_message = self.tr(message)
        prefix = self.tr("The photo library could not be opened during startup: %1")
        self._startup_recovery_label.setText(
            prefix.replace("%1", translated_message)
        )
        self._startup_recovery_details.setText(details or message)
        self._startup_recovery_details.hide()
        self._startup_continue_button.setText(self.tr("Continue without library"))
        self._startup_retry_button.setVisible(
            suggested_action == "retry" and callable(retry_callback)
        )
        self._startup_continue_button.show()
        self._startup_recovery_panel.show()

    def show_startup_warning(self, message: str, *, details: str | None = None) -> None:
        """Show a non-blocking notice after automatic startup recovery."""

        if not hasattr(self, "_startup_recovery_panel"):
            return
        self._startup_retry_callback = None
        self._startup_recovery_label.setText(self.tr(message))
        self._startup_recovery_details.setText(details or message)
        self._startup_recovery_details.hide()
        self._startup_retry_button.hide()
        self._startup_continue_button.setText(self.tr("Dismiss"))
        self._startup_continue_button.show()
        self._startup_recovery_panel.show()

    def _retry_startup(self) -> None:
        callback = self._startup_retry_callback
        self._startup_recovery_panel.hide()
        if callable(callback):
            QTimer.singleShot(0, callback)

    def _on_feature_created(self, feature: str, _widget: object) -> None:
        if feature == FeatureKind.DETAIL.value and self.window_manager is not None:
            self.window_manager.bind_detail_feature()

    def retranslate_ui_tree(self) -> None:
        """Refresh this window and child widgets after translator changes."""

        self.ui.retranslateUi(self)
        handled_widgets = {getattr(self.ui, "main_header", None)}
        for child in self.findChildren(QWidget):
            if child in handled_widgets:
                continue
            method = getattr(child, "retranslate_ui", None)
            if callable(method):
                method()
        if self.window_manager is not None:
            self.window_manager.retranslate_ui()

    def _schedule_retranslate_ui_tree(self) -> None:
        """Refresh after active popup menus finish processing the trigger."""

        QTimer.singleShot(0, self.retranslate_ui_tree)

    def bind_coordinators(self, lifecycle, gallery, detail) -> None:
        """Bind the explicit lifecycle, Gallery, and Detail coordinator ports."""

        self._coordinator_lifecycle = lifecycle
        self._gallery_coordinator = gallery
        self._detail_coordinator = detail
        if self.window_manager is not None:
            self.window_manager.set_detail_coordinator(detail)

    # ------------------------------------------------------------------
    # QWidget overrides
    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        """Tear down background services before the window closes."""

        if self._shutdown_complete:
            super().closeEvent(event)
            return
        cancel_startup = getattr(self._startup_orchestrator, "cancel", None)
        if callable(cancel_startup):
            cancel_startup()
        if not self._is_shutting_down:
            self._is_shutting_down = True
            try:
                if self.window_manager is not None:
                    self.window_manager.cleanup()
                if self._coordinator_lifecycle:
                    self._coordinator_lifecycle.shutdown()
            finally:
                self._shutdown_complete = True
                self._is_shutting_down = False
        super().closeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        """Publish the real first-frame boundary exactly once."""

        super().paintEvent(event)
        if self._first_paint_emitted or not self.isVisible():
            return
        self._first_paint_emitted = True
        from ...bootstrap.startup_profile import mark

        mark("main_window.first_paint")
        self.firstPainted.emit()

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # ``setupUi`` can emit a resize event before the frameless manager is
        # constructed.  Guard against that early call.
        if self.window_manager is not None:
            self.window_manager.handle_resize_event(event)

    def changeEvent(self, event: QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if self.window_manager is not None:
            self.window_manager.handle_change_event(event)

    # ------------------------------------------------------------------
    # Window chrome accessors used by child widgets
    def statusBar(self) -> ChromeStatusBar:  # type: ignore[override]
        """Return the custom status bar embedded in the rounded shell."""

        return self.ui.status_bar

    def menuBar(self) -> QMenuBar:  # type: ignore[override]
        """Expose the menu bar hosted inside the rounded window shell."""

        if self.window_manager is None:
            return super().menuBar()
        return self.window_manager.menuBar()

    def menu_stylesheet(self) -> str | None:
        """Return the cached ``QMenu`` stylesheet so other widgets can reuse it."""

        if self.window_manager is None:
            return None
        return self.window_manager.menu_stylesheet()

    def get_qmenu_stylesheet(self) -> str | None:
        """Expose the rounded ``QMenu`` stylesheet, rebuilding it if necessary."""

        if self.window_manager is None:
            return None
        return self.window_manager.get_qmenu_stylesheet()

    # ------------------------------------------------------------------
    # Convenience wrappers kept for backwards compatibility
    def position_live_badge(self) -> None:
        """Allow legacy callers to reposition the Live badge."""

        if self.window_manager is not None:
            self.window_manager.position_live_badge()

    def position_resize_widgets(self) -> None:
        """Allow legacy callers to reposition the resize affordances."""

        if self.window_manager is not None:
            self.window_manager.position_resize_widgets()

    def toggle_fullscreen(self) -> None:
        """Toggle the immersive full screen mode."""

        if self.window_manager is not None:
            self.window_manager.toggle_fullscreen()

    def enter_fullscreen(self) -> None:
        """Expand the window into the immersive presentation mode."""

        if self.window_manager is not None:
            self.window_manager.enter_fullscreen()

    def exit_fullscreen(self) -> None:
        """Restore the standard chrome from immersive mode."""

        if self.window_manager is not None:
            self.window_manager.exit_fullscreen()

    # ------------------------------------------------------------------
    # Public API used by sidebar/actions
    def open_album_from_path(self, path: Path) -> None:
        """Forward directory navigation through the Gallery port."""
        if self._gallery_coordinator:
            self._gallery_coordinator.open_album_from_path(path)

    def current_selection(self) -> list[Path]:
        """Return absolute paths for every asset selected in the active view."""

        # Priority 1: Grid View (Gallery)
        if self.ui.grid_view.selectionModel() is not None:
            grid_indexes = self.ui.grid_view.selectionModel().selectedIndexes()
            if grid_indexes:
                if self._gallery_coordinator:
                    return self._gallery_coordinator.paths_from_indexes(grid_indexes)

        # Priority 2: Filmstrip View
        if self.ui.filmstrip_view.selectionModel() is not None:
            indexes = self.ui.filmstrip_view.selectionModel().selectedIndexes()
            if indexes:
                if self._gallery_coordinator:
                    return self._gallery_coordinator.paths_from_indexes(indexes)

        return []
