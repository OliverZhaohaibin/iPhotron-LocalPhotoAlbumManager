"""Helpers responsible for the frameless main window chrome."""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Iterable, Iterator, TYPE_CHECKING, cast

from PySide6.QtCore import Property, QEvent, QObject, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPalette,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QMenuBar,
    QVBoxLayout,
    QWidget,
)

from .icon import load_icon
from .styles import modern_scrollbar_style
from .widgets.custom_tooltip import FloatingToolTip, ToolTipEventFilter
from .window_shell import RoundedWindowShell

if TYPE_CHECKING:  # pragma: no cover - used only for type checking
    from PySide6.QtGui import QResizeEvent

    from .ui_main_window import Ui_MainWindow
    from ..coordinators.main_coordinator import MainCoordinator
    from ..coordinators.edit_coordinator import EditCoordinator


# ``PLAYBACK_RESUME_DELAY_MS`` mirrors the behaviour found in the original
# ``MainWindow`` implementation.  The small pause gives Qt time to settle the
# window transition (for example switching into or out of full screen) before
# multimedia playback resumes.  Skipping the delay causes videos to stutter
# visibly on macOS and Windows when the compositor is still applying the size
# changes.
_LOGGER = logging.getLogger(__name__)

PLAYBACK_RESUME_DELAY_MS = 120
_MIN_WINDOW_WIDTH = 900
_MIN_WINDOW_HEIGHT = 640
_SCREEN_CLAMP_MARGIN = 40


class FramelessWindowManager(QObject):
    """Encapsulate the custom chrome applied to the main application window."""

    def __init__(self, window: QMainWindow, ui: Ui_MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._ui = ui
        self._controller: MainCoordinator | None = None

        # Frameless setup -------------------------------------------------
        self._window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._window.setAutoFillBackground(False)

        self._window_corner_radius = 12
        self._rounded_shell = self._create_shell()
        self._size_grip = getattr(self._ui, "size_grip", None)
        self._window_tooltip = FloatingToolTip(self._window)
        self._tooltip_filter: ToolTipEventFilter | None = None
        self._drag_sources: set[QWidget] = set()
        self._resize_sources: set[QWidget] = set()

        self._immersive_active = False
        self._hidden_widget_states: list[tuple[QWidget, bool]] = []
        self._splitter_sizes: list[int] | None = None
        self._previous_geometry = self._window.saveGeometry()
        self._previous_window_state = self._window.windowState()
        self._drag_active = False
        self._system_move_armed = False
        self._drag_trace_id = 0
        self._drag_offset = QPoint()
        self._geometry_fix_in_progress = False
        self._tracked_window_handle: QObject | None = None
        self._last_screen_dpr = 1.0
        self._video_controls_enabled_before = self._ui.video_area.controls_enabled()
        self._window_shell_stylesheet = self._ui.window_shell.styleSheet()
        self._player_container_stylesheet = self._ui.player_container.styleSheet()
        self._player_stack_stylesheet = self._ui.player_stack.styleSheet()
        self._immersive_background_applied = False
        self._immersive_visibility_targets = self._build_immersive_targets()

        self._qmenu_stylesheet: str = ""
        self._global_menu_stylesheet: str | None = None
        self._applying_menu_styles = False

        self._install_tooltip_filter()
        self._configure_window_controls()
        self._configure_drag_sources()
        self._apply_menu_styles()
        self.position_live_badge()
        self.position_resize_widgets()
        QTimer.singleShot(0, self._init_screen_tracking)

    # ------------------------------------------------------------------
    # Lifecycle helpers
    def set_controller(self, controller: MainCoordinator) -> None:
        """Provide the coordinator reference required for immersive mode."""

        self._controller = controller

    def cleanup(self) -> None:
        """Remove global filters and hide tooltip widgets during shutdown."""

        app = QApplication.instance()
        if app is not None:
            if self._tooltip_filter is not None:
                app.removeEventFilter(self._tooltip_filter)
                if app.property("floatingToolTipFilter") == self._tooltip_filter:
                    app.setProperty("floatingToolTipFilter", None)
        self._tooltip_filter = None
        self._window_tooltip.hide_tooltip()

    # ------------------------------------------------------------------
    # Menu helpers
    def menuBar(self) -> QMenuBar:
        """Expose the menu bar hosted inside the rounded shell."""

        return self._ui.menu_bar

    def menu_stylesheet(self) -> str | None:
        """Return the cached ``QMenu`` stylesheet so other widgets can reuse it."""

        return self.get_qmenu_stylesheet()

    def get_qmenu_stylesheet(self) -> str | None:
        """Expose the rounded ``QMenu`` stylesheet, rebuilding it if necessary."""

        if not self._qmenu_stylesheet:
            if not self._applying_menu_styles:
                self._apply_menu_styles()
            else:
                self._qmenu_stylesheet = self._build_menu_styles()[0]
        return self._qmenu_stylesheet or None

    # ------------------------------------------------------------------
    # Event forwarding helpers used by ``MainWindow``
    def handle_resize_event(self, event: QResizeEvent) -> None:
        """Reposition overlays whenever the window geometry changes."""

        _ = event  # ``QResizeEvent`` is unused but kept for signature clarity.
        self.position_live_badge()
        self.position_resize_widgets()
        self._clamp_window_to_current_screen()

    def handle_change_event(self, event: QEvent) -> None:
        """Update palette-dependent chrome when Qt notifies about state changes."""

        if event.type() == QEvent.Type.WindowTitleChange:
            self._update_title_bar()
        elif event.type() == QEvent.Type.PaletteChange:
            self._rounded_shell.update()
            self._apply_menu_styles()

    # ------------------------------------------------------------------
    # Window chrome helpers
    def position_live_badge(self) -> None:
        """Keep the Live badge pinned to the player corner."""

        if self._ui.badge_host is None:
            return
        self._ui.live_badge.move(15, 15)
        self._ui.live_badge.raise_()

    def position_resize_widgets(self) -> None:
        """Pin the resize icon and grip to the shell's lower-right corner."""

        shell = getattr(self, "_rounded_shell", None)
        if shell is None:
            return

        indicator = getattr(self._ui, "resize_indicator", None)
        size_grip = getattr(self, "_size_grip", None)

        margin = 5
        width_candidates = [
            widget.width() for widget in (indicator, size_grip) if widget is not None
        ]
        height_candidates = [
            widget.height() for widget in (indicator, size_grip) if widget is not None
        ]
        if not width_candidates or not height_candidates:
            return

        target_width = max(width_candidates)
        target_height = max(height_candidates)
        target_x = max(0, shell.width() - target_width - margin)
        target_y = max(0, shell.height() - target_height - margin)

        if size_grip is not None:
            size_grip.move(target_x, target_y)
            size_grip.raise_()

        if indicator is not None:
            indicator.move(target_x, target_y)
            indicator.raise_()

    def toggle_fullscreen(self) -> None:
        """Toggle the immersive full screen mode."""

        edit_controller = self._edit_controller()
        if (
            edit_controller is not None
            and self._controller is not None
            and self._controller.is_edit_view_active()
            and edit_controller.is_editing()
        ):
            if edit_controller.is_in_fullscreen():
                edit_controller.exit_fullscreen_preview()
            else:
                edit_controller.enter_fullscreen_preview()
            return

        if self._immersive_active:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self) -> None:
        """Expand the window into an immersive, chrome-free full screen mode."""

        edit_controller = self._edit_controller()
        if (
            edit_controller is not None
            and self._controller is not None
            and self._controller.is_edit_view_active()
            and edit_controller.is_editing()
        ):
            edit_controller.enter_fullscreen_preview()
            return

        if self._immersive_active:
            return
        if self._controller is None:
            return

        resume_after_transition = self._controller.suspend_playback_for_transition()
        ready = self._controller.prepare_fullscreen_asset()
        if not ready:
            self._controller.show_placeholder_in_viewer()

        self._previous_geometry = self._window.saveGeometry()
        self._previous_window_state = self._window.windowState()
        self._splitter_sizes = self._ui.splitter.sizes()
        with self._suspend_layout_updates():
            self._hidden_widget_states = self._override_visibility(
                self._immersive_visibility_targets, visible=False
            )

            self._video_controls_enabled_before = (
                self._ui.video_area.controls_enabled()
            )
            self._ui.video_area.hide_controls(animate=False)
            self._ui.splitter.setSizes([0, sum(self._splitter_sizes or [self._window.width()])])

        self._apply_immersive_backdrop()

        self._immersive_active = True
        self._window.showFullScreen()
        self._update_fullscreen_button_icon()
        self._schedule_playback_resume(
            expect_immersive=True, resume=resume_after_transition
        )

    def exit_fullscreen(self) -> None:
        """Restore the normal window chrome and previously visible widgets."""

        edit_controller = self._edit_controller()
        if (
            edit_controller is not None
            and edit_controller.is_editing()
            and edit_controller.is_in_fullscreen()
        ):
            edit_controller.exit_fullscreen_preview()
            return

        if not self._immersive_active:
            return
        if self._controller is None:
            return

        resume_after_transition = self._controller.suspend_playback_for_transition()
        self._immersive_active = False
        self._restore_default_backdrop()
        self._window.showNormal()

        with self._suspend_layout_updates():
            if self._previous_geometry is not None:
                self._window.restoreGeometry(self._previous_geometry)
            if self._previous_window_state is not None:
                self._window.setWindowState(self._previous_window_state)
            if self._splitter_sizes:
                self._ui.splitter.setSizes(self._splitter_sizes)

            for widget, was_visible in self._hidden_widget_states:
                widget.setVisible(was_visible)
            self._hidden_widget_states = []

            self._ui.video_area.set_controls_enabled(
                self._video_controls_enabled_before
            )
            if (
                self._video_controls_enabled_before
                and self._ui.video_area.isVisible()
            ):
                self._ui.video_area.show_controls(animate=False)

        edit_controller = self._edit_controller()
        if (
            self._ui.view_stack.currentWidget() is self._ui.detail_page
            and (edit_controller is None or not edit_controller.is_editing())
        ):
            if self._ui.detail_chrome_container is not None:
                self._ui.detail_chrome_container.show()
            if self._ui.toggle_filmstrip_action is not None:
                self._ui.filmstrip_view.setVisible(
                    self._ui.toggle_filmstrip_action.isChecked()
                )

        self._update_fullscreen_button_icon()
        self._schedule_playback_resume(
            expect_immersive=False, resume=resume_after_transition
        )

    def is_immersive_active(self) -> bool:
        """Return ``True`` when the window is in immersive full screen mode."""

        return self._immersive_active

    def _edit_controller(self) -> "EditCoordinator | None":
        """Return the edit coordinator if the main coordinator exposes one."""

        if self._controller is None:
            return None
        accessor = getattr(self._controller, "edit_controller", None)
        if callable(accessor):
            return accessor()
        return None

    # ------------------------------------------------------------------
    # QObject overrides
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        """Handle title-bar dragging and badge positioning."""

        if watched in self._resize_sources:
            if self._handle_resize_source_event(event):
                return True

        if watched in self._drag_sources:
            if self._handle_title_bar_drag(event):
                return True

        if watched is self._ui.badge_host and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.Show,
        }:
            self.position_live_badge()

        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # Internal helpers
    def _create_shell(self) -> RoundedWindowShell:
        original_shell = self._ui.window_shell
        rounded_shell = RoundedWindowShell(radius=self._window_corner_radius, parent=self._window)
        rounded_shell.setPalette(self._window.palette())
        original_shell.setParent(rounded_shell)
        original_shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        original_shell.setAutoFillBackground(False)
        original_shell.setStyleSheet("background-color: transparent;")
        cast(QVBoxLayout, rounded_shell.layout()).addWidget(original_shell)
        self._window.setCentralWidget(rounded_shell)

        resize_indicator = getattr(self._ui, "resize_indicator", None)
        if resize_indicator is not None:
            resize_indicator.setParent(rounded_shell)
            resize_indicator.show()

        size_grip = getattr(self._ui, "size_grip", None)
        if size_grip is not None:
            size_grip.setParent(rounded_shell)
            size_grip.show()
        return rounded_shell

    def _install_tooltip_filter(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        self._tooltip_filter = ToolTipEventFilter(self._window_tooltip, parent=self)
        app.installEventFilter(self._tooltip_filter)
        app.setProperty("floatingToolTipFilter", self._tooltip_filter)

    def _configure_window_controls(self) -> None:
        self._ui.minimize_button.clicked.connect(self._window.showMinimized)
        self._ui.close_button.clicked.connect(self._window.close)
        self._ui.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        self._ui.image_viewer.fullscreenToggleRequested.connect(self.toggle_fullscreen)
        self._ui.image_viewer.fullscreenExitRequested.connect(self.exit_fullscreen)
        self._ui.video_area.fullscreenExitRequested.connect(self.exit_fullscreen)

    def _configure_drag_sources(self) -> None:
        self._drag_sources = {self._ui.title_bar, self._ui.window_title_label}
        for source in self._drag_sources:
            source.installEventFilter(self)

        size_grip = getattr(self, "_size_grip", None)
        resize_indicator = getattr(self._ui, "resize_indicator", None)
        self._resize_sources = {
            source for source in (size_grip, resize_indicator) if source is not None
        }
        for source in self._resize_sources:
            source.installEventFilter(self)

        self._ui.badge_host.installEventFilter(self)

    def _init_screen_tracking(self) -> None:
        handle = self._window.windowHandle()
        if handle is None:
            QTimer.singleShot(0, self._init_screen_tracking)
            return
        if self._tracked_window_handle is handle:
            return

        if self._tracked_window_handle is not None:
            try:
                self._tracked_window_handle.screenChanged.disconnect(self._on_screen_changed)
            except (RuntimeError, TypeError):
                pass

        self._tracked_window_handle = handle
        handle.screenChanged.connect(self._on_screen_changed)
        screen = handle.screen()
        self._last_screen_dpr = self._screen_dpr(screen)
        self._clamp_window_to_screen(screen)

    def _on_screen_changed(self, new_screen: object) -> None:
        if self._geometry_fix_in_progress:
            return

        old_dpr = self._last_screen_dpr or 1.0
        QTimer.singleShot(0, lambda: self._apply_screen_change_fix(old_dpr, new_screen))

    def _apply_screen_change_fix(self, old_dpr: float, new_screen: object) -> None:
        if self._geometry_fix_in_progress:
            return

        available = self._available_rect(new_screen)
        if available is None:
            self._last_screen_dpr = 1.0
            return

        new_dpr = self._screen_dpr(new_screen)
        current = self._window.size()
        scaled = QSize(
            int(current.width() * old_dpr / new_dpr),
            int(current.height() * old_dpr / new_dpr),
        )
        target = self._clamp_size_to_available(scaled, available.width(), available.height())

        self._geometry_fix_in_progress = True
        try:
            if target != current:
                self._window.resize(target)

            if not available.intersects(self._window.frameGeometry()):
                self._window.move(available.x() + 20, available.y() + 20)
        finally:
            self._geometry_fix_in_progress = False

        self._last_screen_dpr = new_dpr

    def _clamp_window_to_current_screen(self) -> None:
        if self._geometry_fix_in_progress:
            return
        handle = self._window.windowHandle()
        screen = handle.screen() if handle is not None else None
        self._clamp_window_to_screen(screen)

    def _clamp_window_to_screen(self, screen: object) -> None:
        available = self._available_rect(screen)
        if available is None:
            return

        clamped = self._clamp_size_to_available(
            self._window.size(), available.width(), available.height()
        )
        if clamped == self._window.size():
            return

        self._geometry_fix_in_progress = True
        try:
            self._window.resize(clamped)
        finally:
            self._geometry_fix_in_progress = False

    @staticmethod
    def _available_rect(screen: object):
        if screen is None or not hasattr(screen, "availableGeometry"):
            return None
        return screen.availableGeometry()

    @staticmethod
    def _screen_dpr(screen: object) -> float:
        if screen is None or not hasattr(screen, "devicePixelRatio"):
            return 1.0
        raw = float(screen.devicePixelRatio())
        return raw if raw > 0.0 else 1.0

    @staticmethod
    def _clamp_size_to_available(size: QSize, avail_w: int, avail_h: int) -> QSize:
        max_w = max(_MIN_WINDOW_WIDTH, avail_w - _SCREEN_CLAMP_MARGIN)
        max_h = max(_MIN_WINDOW_HEIGHT, avail_h - _SCREEN_CLAMP_MARGIN)
        width = max(_MIN_WINDOW_WIDTH, min(size.width(), max_w))
        height = max(_MIN_WINDOW_HEIGHT, min(size.height(), max_h))
        return QSize(width, height)


    def _log_windows_snap_diagnostics(self, stage: str, **extra: object) -> None:
        """Emit debug details for Windows frameless move/resize troubleshooting."""

        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return

        handle = self._window.windowHandle()
        payload = {
            "stage": stage,
            "platform": sys.platform,
            "has_handle": handle is not None,
            "can_system_move": bool(handle and hasattr(handle, "startSystemMove")),
            "can_system_resize": bool(handle and hasattr(handle, "startSystemResize")),
            **extra,
        }
        details = ", ".join(f"{key}={value}" for key, value in payload.items())
        _LOGGER.debug("Frameless snap diagnostic: %s", details)


    def _log_windows_snap_trace(self, stage: str, **extra: object) -> None:
        """Emit info-level drag trace logs to diagnose Windows snapping attempts."""

        if sys.platform != "win32":
            return
        payload = {"stage": stage, "trace": self._drag_trace_id, **extra}
        details = ", ".join(f"{key}={value}" for key, value in payload.items())
        _LOGGER.info("Frameless snap trace: %s", details)

    def _handle_title_bar_drag(self, event: QEvent) -> bool:
        if self._immersive_active:
            return False

        if event.type() == QEvent.Type.MouseButtonPress:
            mouse_event = cast(QMouseEvent, event)
            if mouse_event.button() == Qt.MouseButton.LeftButton:
                self._drag_active = True
                self._drag_trace_id += 1
                self._log_windows_snap_trace("title_press", global_pos=mouse_event.globalPosition().toPoint())
                self._log_windows_snap_diagnostics("title_press", button="left")
                self._system_move_armed = sys.platform == "win32"
                self._drag_offset = (
                    mouse_event.globalPosition().toPoint()
                    - self._window.frameGeometry().topLeft()
                )
                if self._system_move_armed and self._start_system_move():
                    self._log_windows_snap_trace("title_press_system_move_started")
                    self._log_windows_snap_diagnostics("title_press_system_move_started")
                    self._drag_active = False
                    self._system_move_armed = False
                    return True
                self._log_windows_snap_trace("title_press_system_move_unavailable", armed=self._system_move_armed)
                self._log_windows_snap_diagnostics("title_press_system_move_unavailable")
                if self._system_move_armed:
                    _LOGGER.warning(
                        "Frameless drag could not start Windows system move on press; will retry on move."
                    )
                return True
        if event.type() == QEvent.Type.MouseMove and self._drag_active:
            mouse_event = cast(QMouseEvent, event)
            if mouse_event.buttons() & Qt.MouseButton.LeftButton:
                if self._system_move_armed and self._start_system_move():
                    self._log_windows_snap_trace("title_move_system_move_started")
                    self._log_windows_snap_diagnostics("title_move_system_move_started")
                    self._drag_active = False
                    self._system_move_armed = False
                    return True
                self._system_move_armed = False
                self._log_windows_snap_trace("title_move_manual_fallback", global_pos=mouse_event.globalPosition().toPoint())
                self._log_windows_snap_diagnostics("title_move_manual_fallback")
                if sys.platform == "win32":
                    _LOGGER.warning(
                        "Frameless drag fell back to manual move on Windows; Snap may not trigger."
                    )
                new_pos = mouse_event.globalPosition().toPoint() - self._drag_offset
                self._window.move(new_pos)
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self._drag_active:
            self._log_windows_snap_trace("title_release")
            self._drag_active = False
            self._system_move_armed = False
            return True
        return False

    def _handle_resize_source_event(self, event: QEvent) -> bool:
        if self._immersive_active:
            return False
        if event.type() != QEvent.Type.MouseButtonPress:
            return False

        mouse_event = cast(QMouseEvent, event)
        if mouse_event.button() != Qt.MouseButton.LeftButton:
            return False

        self._log_windows_snap_trace("resize_press")
        started = self._start_system_resize(Qt.Edge.BottomEdge | Qt.Edge.RightEdge)
        self._log_windows_snap_trace("resize_press_result", started=started)
        self._log_windows_snap_diagnostics("resize_press", started=started)
        if sys.platform == "win32" and not started:
            _LOGGER.warning(
                "Frameless resize could not start Windows system resize from grip."
            )
        return started

    def _start_system_move(self) -> bool:
        if sys.platform != "win32":
            self._log_windows_snap_diagnostics("start_system_move_skipped_non_windows")
            return False
        handle = self._window.windowHandle()
        if handle is None or not hasattr(handle, "startSystemMove"):
            self._log_windows_snap_diagnostics("start_system_move_missing_api")
            return False
        started = bool(handle.startSystemMove())
        self._log_windows_snap_trace("start_system_move_result", started=started)
        self._log_windows_snap_diagnostics("start_system_move_result", started=started)
        return started

    def _start_system_resize(self, edges: Qt.Edge) -> bool:
        if sys.platform != "win32":
            self._log_windows_snap_diagnostics("start_system_resize_skipped_non_windows")
            return False
        handle = self._window.windowHandle()
        if handle is None or not hasattr(handle, "startSystemResize"):
            self._log_windows_snap_diagnostics("start_system_resize_missing_api")
            return False
        started = bool(handle.startSystemResize(edges))
        self._log_windows_snap_trace("start_system_resize_result", started=started, edges=repr(edges))
        self._log_windows_snap_diagnostics(
            "start_system_resize_result", started=started, edges=repr(edges)
        )
        return started

    def _update_fullscreen_button_icon(self) -> None:
        if self._immersive_active:
            self._ui.fullscreen_button.setIcon(load_icon("green.restore.circle.svg"))
            self._ui.fullscreen_button.setToolTip("Exit Full Screen")
        else:
            self._ui.fullscreen_button.setIcon(load_icon("green.maximum.circle.svg"))
            self._ui.fullscreen_button.setToolTip("Enter Full Screen")

    def _update_title_bar(self) -> None:
        self._ui.window_title_label.setText(self._window.windowTitle())

    def _apply_immersive_backdrop(self) -> None:
        if self._immersive_background_applied:
            return

        self._window_shell_stylesheet = self._ui.window_shell.styleSheet()
        self._player_container_stylesheet = self._ui.player_container.styleSheet()
        self._player_stack_stylesheet = self._ui.player_stack.styleSheet()

        self._rounded_shell.set_corner_radius(0)
        self._rounded_shell.set_override_color(QColor("#000000"))
        self._ui.window_shell.setStyleSheet("background-color: #000000;")
        self._ui.player_container.setStyleSheet("background-color: #000000;")
        self._ui.player_stack.setStyleSheet("background-color: #000000;")
        self._ui.image_viewer.set_immersive_background(True)
        self._ui.video_area.set_immersive_background(True)
        self._immersive_background_applied = True

    def _restore_default_backdrop(self) -> None:
        if not self._immersive_background_applied:
            return

        self._ui.window_shell.setStyleSheet(self._window_shell_stylesheet)
        self._ui.player_container.setStyleSheet(self._player_container_stylesheet)
        self._ui.player_stack.setStyleSheet(self._player_stack_stylesheet)
        self._ui.image_viewer.set_immersive_background(False)
        self._ui.video_area.set_immersive_background(False)
        self._rounded_shell.set_override_color(None)
        self._rounded_shell.set_corner_radius(self._window_corner_radius)
        self._immersive_background_applied = False

    def _schedule_playback_resume(self, *, expect_immersive: bool, resume: bool) -> None:
        if not resume:
            return
        if self._controller is None:
            return

        def _resume() -> None:
            if self._immersive_active != expect_immersive:
                return
            self._controller.resume_playback_after_transition()

        QTimer.singleShot(PLAYBACK_RESUME_DELAY_MS, _resume)

    @contextmanager
    def _suspend_layout_updates(self) -> Iterator[None]:
        updates_previously_enabled = self._window.updatesEnabled()
        splitter_signals_blocked = self._ui.splitter.signalsBlocked()
        self._window.setUpdatesEnabled(False)
        self._ui.splitter.blockSignals(True)
        try:
            yield
        finally:
            self._ui.splitter.blockSignals(splitter_signals_blocked)
            self._window.setUpdatesEnabled(updates_previously_enabled)
            if updates_previously_enabled:
                self._window.update()

    def _override_visibility(
        self, widgets: Iterable[QWidget], *, visible: bool
    ) -> list[tuple[QWidget, bool]]:
        previous_states: list[tuple[QWidget, bool]] = []
        for widget in widgets:
            previous_states.append((widget, widget.isVisible()))
            widget.setVisible(visible)
        return previous_states

    def _build_immersive_targets(self) -> tuple[QWidget, ...]:
        candidates: tuple[QWidget | None, ...] = (
            self.menuBar(),
            self._ui.menu_bar_container,
            self._ui.status_bar,
            self._ui.sidebar,
            self._ui.window_chrome,
            self._ui.detail_chrome_container,
            self._ui.filmstrip_view,
        )
        return tuple(widget for widget in candidates if widget is not None)

    def _build_menu_styles(self) -> tuple[str, str]:
        palette = self._rounded_shell.palette()
        window_color = self._opaque_color(palette.color(QPalette.ColorRole.Window))
        border_color = self._opaque_color(palette.color(QPalette.ColorRole.Mid))
        text_color = self._opaque_color(palette.color(QPalette.ColorRole.WindowText))
        highlight_color = self._opaque_color(palette.color(QPalette.ColorRole.Highlight))
        highlight_text_color = self._opaque_color(
            palette.color(QPalette.ColorRole.HighlightedText)
        )
        separator_color = self._opaque_color(palette.color(QPalette.ColorRole.Midlight))

        window_color_name = window_color.name()
        border_color_name = border_color.name()
        text_color_name = text_color.name()
        highlight_color_name = highlight_color.name()
        highlight_text_color_name = highlight_text_color.name()
        separator_color_name = separator_color.name()

        border_radius_px = 8
        item_radius_px = max(0, border_radius_px - 3)

        qmenu_style = (
            "QMenu {\n"
            f"    background-color: {window_color_name};\n"
            f"    border: 1px solid {border_color_name};\n"
            f"    border-radius: {border_radius_px}px;\n"
            "    padding: 4px;\n"
            "    margin: 0px;\n"
            "}\n"
            "QMenu::item {\n"
            "    background-color: transparent;\n"
            f"    color: {text_color_name};\n"
            "    padding: 5px 20px;\n"
            "    margin: 2px 6px;\n"
            f"    border-radius: {item_radius_px}px;\n"
            "}\n"
            "QMenu::item:selected {\n"
            f"    background-color: {highlight_color_name};\n"
            f"    color: {highlight_text_color_name};\n"
            "}\n"
            "QMenu::separator {\n"
            "    height: 1px;\n"
            f"    background: {separator_color_name};\n"
            "    margin: 4px 10px;\n"
            "}"
        )

        selectors = (
            ", QWidget QScrollBar:vertical, QAbstractScrollArea QScrollBar:vertical, "
            "QListView QScrollBar:vertical, QTreeView QScrollBar:vertical, "
            "QTableView QScrollBar:vertical, QScrollArea QScrollBar:vertical, "
            "#galleryGridView QScrollBar:vertical"
        )
        scrollbar_style = modern_scrollbar_style(text_color, extra_selectors=selectors)

        qmenu_style = qmenu_style + "\n" + scrollbar_style

        menubar_style = (
            "QMenuBar {\n"
            f"    background-color: {window_color_name};\n"
            "    border-radius: 0px;\n"
            "    padding: 2px;\n"
            "}\n"
            "QMenuBar::item {\n"
            "    background-color: transparent;\n"
            f"    color: {text_color_name};\n"
            "    padding: 4px 10px;\n"
            "    border-radius: 4px;\n"
            "}\n"
            "QMenuBar::item:selected {\n"
            f"    background-color: {highlight_color_name};\n"
            f"    color: {highlight_text_color_name};\n"
            "}\n"
            "QMenuBar::separator {\n"
            f"    background: {separator_color_name};\n"
            "    width: 1px;\n"
            "    margin: 4px 2px;\n"
            "}"
        )

        self._qmenu_stylesheet = qmenu_style
        return qmenu_style, menubar_style

    @staticmethod
    def _opaque_color(color: QColor) -> QColor:
        if color.alpha() >= 255:
            return color
        opaque_color = QColor(color)
        opaque_color.setAlpha(255)
        return opaque_color

    def _configure_popup_menu(self, menu: QMenu, stylesheet: str) -> None:
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        menu.setAutoFillBackground(True)
        menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        menu.setWindowFlag(Qt.WindowType.Popup, True)
        menu.setPalette(self._window.palette())
        menu.setBackgroundRole(QPalette.ColorRole.Base)
        menu.setStyleSheet(stylesheet)
        menu.setGraphicsEffect(None)

    def _apply_menu_styles(self) -> None:
        if self._applying_menu_styles:
            return

        self._applying_menu_styles = True
        try:
            qmenu_style, menubar_style = self._build_menu_styles()
            self._ui.menu_bar.setStyleSheet(menubar_style)
            self._ui.menu_bar.setAutoFillBackground(True)
            self._ui.menu_bar.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, False
            )

            app = QApplication.instance()
            if app is not None:
                existing = app.styleSheet()
                if self._global_menu_stylesheet and self._global_menu_stylesheet in existing:
                    existing = existing.replace(self._global_menu_stylesheet, "").strip()

                combined_parts = [part for part in (existing, qmenu_style) if part]
                app.setStyleSheet("\n".join(combined_parts))
                self._global_menu_stylesheet = qmenu_style
            else:
                self._global_menu_stylesheet = qmenu_style

            for action in self._ui.menu_bar.actions():
                menu = action.menu()
                if menu is None:
                    continue
                self._configure_popup_menu(menu, qmenu_style)
        finally:
            self._applying_menu_styles = False
