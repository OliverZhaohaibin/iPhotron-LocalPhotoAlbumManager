"""Shared helper primitives for Qt map widget transport and overlays."""

from __future__ import annotations

from logging import getLogger
from typing import Callable

from PySide6.QtCore import QObject, QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QWidget


LOGGER = getLogger(__name__)


class MapEventSurfaceBridge:
    """Bind one owner event filter to a map widget and its event target."""

    def __init__(
        self,
        owner: QObject,
        *,
        install_application_filter: bool = False,
    ) -> None:
        self._owner = owner
        self._install_application_filter = bool(install_application_filter)
        self._targets: list[QObject] = []
        self._event_target: QObject | None = None
        self._application_filter_installed = False

    def bind(self, map_widget: object) -> tuple[QObject, ...]:
        """Install the owner as the active filter for *map_widget* surfaces."""

        self.unbind()

        targets: list[QObject] = []
        event_target: QObject | None = None
        if isinstance(map_widget, QObject):
            targets.append(map_widget)

        event_target_getter = getattr(map_widget, "event_target", None)
        if callable(event_target_getter):
            try:
                candidate = event_target_getter()
            except Exception:  # noqa: BLE001 - GUI helper must stay best-effort
                LOGGER.debug("Failed to resolve map widget event target", exc_info=True)
            else:
                if isinstance(candidate, QObject):
                    event_target = candidate
                    if not any(candidate is existing for existing in targets):
                        targets.append(candidate)

        for target in targets:
            target.installEventFilter(self._owner)

        if self._install_application_filter:
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self._owner)
                self._application_filter_installed = True

        self._targets = targets
        self._event_target = event_target or (targets[0] if targets else None)
        return tuple(self._targets)

    def unbind(self) -> None:
        """Remove any currently registered event filters."""

        for target in self._targets:
            try:
                target.removeEventFilter(self._owner)
            except RuntimeError:
                continue

        self._targets = []
        self._event_target = None

        app = QApplication.instance()
        if app is not None and self._application_filter_installed:
            app.removeEventFilter(self._owner)
        self._application_filter_installed = False

    def targets(self) -> tuple[QObject, ...]:
        return tuple(self._targets)

    def event_target(self) -> QObject | None:
        return self._event_target

    @property
    def application_filter_installed(self) -> bool:
        return self._application_filter_installed


class MapOverlayAttachment:
    """Manage post-render painter attachment with QWidget overlay fallback."""

    def __init__(self) -> None:
        self._callback: Callable[[QPainter], None] | None = None
        self._uses_post_render = False

    @staticmethod
    def supports_post_render(map_widget: object) -> bool:
        add_post_render_painter = getattr(map_widget, "add_post_render_painter", None)
        supports_post_render_painter = getattr(
            map_widget,
            "supports_post_render_painter",
            lambda: True,
        )
        return callable(add_post_render_painter) and supports_post_render_painter()

    def attach(
        self,
        map_widget: object,
        *,
        callback: Callable[[QPainter], None] | None,
        overlay: QWidget | None = None,
        overlay_geometry: QRect | None = None,
        raise_overlay: bool = False,
    ) -> bool:
        """Attach *callback* if supported, otherwise activate *overlay*."""

        self._callback = None
        self._uses_post_render = False

        if callback is not None and self.supports_post_render(map_widget):
            add_post_render_painter = getattr(map_widget, "add_post_render_painter")
            add_post_render_painter(callback)
            self._callback = callback
            self._uses_post_render = True
            if overlay is not None:
                overlay.hide()
            return True

        self.sync_widget_overlay(
            overlay,
            geometry=overlay_geometry,
            raise_overlay=raise_overlay,
        )
        return False

    def sync_widget_overlay(
        self,
        overlay: QWidget | None,
        *,
        geometry: QRect | None = None,
        raise_overlay: bool = False,
    ) -> None:
        """Keep the QWidget overlay aligned with the current map geometry."""

        if self._uses_post_render or overlay is None:
            return
        if geometry is not None:
            overlay.setGeometry(geometry)
        if raise_overlay:
            overlay.raise_()

    def detach(self, map_widget: object) -> None:
        """Remove any active post-render painter from *map_widget*."""

        callback = self._callback
        if callback is not None:
            remove_post_render_painter = getattr(
                map_widget,
                "remove_post_render_painter",
                None,
            )
            if callable(remove_post_render_painter):
                remove_post_render_painter(callback)
        self._callback = None
        self._uses_post_render = False

    @property
    def callback(self) -> Callable[[QPainter], None] | None:
        return self._callback

    @property
    def uses_post_render(self) -> bool:
        return self._uses_post_render
