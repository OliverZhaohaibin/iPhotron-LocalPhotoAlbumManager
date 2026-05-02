"""Composite widget that embeds the map preview and renders photo markers."""

from __future__ import annotations

from logging import getLogger
from pathlib import Path
from typing import Dict, Iterable, Optional, cast

from PySide6.QtCore import QObject, QRectF, Qt, QEvent, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPalette,
)
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ....application.ports import MapInteractionServicePort, MapRuntimePort
from ....application.services.map_interaction_service import LibraryMapInteractionService
from maps.map_widget.map_renderer import CityAnnotation

from ....library.manager import GeotaggedAsset
from ..tasks.thumbnail_loader import ThumbnailLoader
from .marker_controller import MarkerController, _MarkerCluster
from .custom_tooltip import FloatingToolTip, ToolTipEventFilter
from .map_widget_factory import (
    MapGLWidget,
    MapGLWindowWidget,
    MapWidget,
    MapWidgetBase,
    MapSourceSpec,
    NativeOsmAndWidget,
    _MAPS_PACKAGE_ROOT,
    check_opengl_support,
    choose_map_widget_backend,
    create_map_widget,
    format_map_runtime_diagnostics,
    resolve_map_package_root,
)


logger = getLogger(__name__)
_MAPS_PACKAGE_ROOT = Path(__file__).resolve().parents[4] / "maps"
_MAP_OPAQUE_BACKGROUND = "#88a8c2"


def _configure_opaque_map_container(
    widget: QWidget,
    *,
    background: str = _MAP_OPAQUE_BACKGROUND,
) -> None:
    """Give map hosts an opaque fallback while their GL child rebuilds."""

    if not widget.objectName():
        widget.setObjectName(type(widget).__name__)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
    widget.setAutoFillBackground(True)
    palette = QPalette(widget.palette())
    palette.setColor(QPalette.ColorRole.Window, QColor(background))
    widget.setPalette(palette)
    widget.setStyleSheet(
        f"QWidget#{widget.objectName()} {{ background-color: {background}; border: none; }}"
    )


class _MarkerLayer(QWidget):
    """Transparent overlay that paints thumbnail clusters with callout arrows."""

    MARKER_SIZE = 72
    THUMBNAIL_NATIVE_SIZE = 192
    THUMBNAIL_DISPLAY_SIZE = 56
    BADGE_DIAMETER = 26
    POINTER_HEIGHT = 10
    POINTER_WIDTH = 18
    CORNER_RADIUS = 12

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # The layer is purely visual, therefore it must not intercept input
        # events which are handled by :class:`PhotoMapView` and the map widget.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._clusters: list[_MarkerCluster] = []
        self._pixmaps: Dict[str, QPixmap] = {}
        self._placeholder = self._create_placeholder()
        self._badge_font = QFont()
        self._badge_font.setBold(True)
        self._badge_pen = QPen(QColor("white"))
        self._badge_pen.setWidth(1)
        self._badge_brush = QColor("#d64541")

    @property
    def marker_size(self) -> int:
        """Return the logical footprint of each marker."""

        return self.MARKER_SIZE

    @property
    def thumbnail_size(self) -> int:
        """Return the requested thumbnail edge length."""

        return self.THUMBNAIL_NATIVE_SIZE

    @property
    def thumbnail_display_size(self) -> int:
        """Return the on-screen pixel edge length used for thumbnails."""

        return self.THUMBNAIL_DISPLAY_SIZE

    def set_clusters(self, items: Iterable[_MarkerCluster]) -> None:
        """Replace the rendered clusters and schedule a repaint."""

        self._clusters = list(items)
        self.update()

    def set_thumbnail(self, rel: str, pixmap: QPixmap) -> None:
        """Cache the pixmap associated with *rel* and refresh the overlay."""

        if pixmap.isNull():
            return
        self._pixmaps[rel] = pixmap
        self.update()

    def clear_pixmaps(self) -> None:
        """Drop cached pixmaps so outdated thumbnails are not reused."""

        self._pixmaps.clear()
        self.update()

    def paint_markers(self, painter: QPainter) -> None:
        """Paint all marker clusters into an already active painter."""

        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for cluster in self._clusters:
            self._paint_cluster(painter, cluster)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        self.paint_markers(painter)
        painter.end()

    def _paint_cluster(self, painter: QPainter, cluster: _MarkerCluster) -> None:
        width = float(self.MARKER_SIZE)
        display_edge = float(self.THUMBNAIL_DISPLAY_SIZE)
        # The callout should surround the thumbnail with an equal white border on all sides.
        # Deriving the border from the configured marker size keeps the geometry consistent
        # when designers tweak either constant while ensuring horizontal and vertical padding
        # always match.
        border = (width - display_edge) / 2.0
        body_height = display_edge + 2.0 * border
        height = body_height + float(self.POINTER_HEIGHT)
        x = cluster.screen_pos.x() - width / 2.0
        y = cluster.screen_pos.y() - height
        rect = QRectF(x, y, width, height)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        path = self._create_callout_path(rect)

        painter.save()
        painter.setPen(QPen(QColor(0, 0, 0, 80), 2))
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.drawPath(path)
        painter.restore()

        thumbnail = self._pixmaps.get(cluster.representative.library_relative)
        if thumbnail is None:
            thumbnail = self._placeholder
        if not thumbnail.isNull():
            thumb_rect = QRectF(
                rect.left() + border,
                rect.top() + border,
                display_edge,
                display_edge,
            )
            painter.save()
            clip_path = QPainterPath()
            # ``setClipPath`` trims the square pixmap into a rounded rectangle so
            # the map overlay mirrors the visual language used by the filmstrip
            # and the rest of the application.
            clip_path.addRoundedRect(thumb_rect, 8.0, 8.0)
            painter.setClipPath(clip_path, Qt.ClipOperation.ReplaceClip)
            painter.drawPixmap(thumb_rect.toRect(), thumbnail)
            painter.restore()

        count = len(cluster.assets)
        if count > 1:
            badge_rect = QRectF(
                rect.right() - self.BADGE_DIAMETER + 4,
                rect.top() - 4,
                self.BADGE_DIAMETER,
                self.BADGE_DIAMETER,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._badge_brush)
            painter.drawEllipse(badge_rect)
            painter.setPen(self._badge_pen)
            painter.setFont(self._badge_font)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(count))

        cluster.bounding_rect = path.boundingRect()

    def _create_callout_path(self, rect: QRectF) -> QPainterPath:
        """Return a speech-bubble style path anchored at the rectangle centre."""

        path = QPainterPath()
        main_rect = QRectF(
            rect.left(),
            rect.top(),
            rect.width(),
            rect.height() - self.POINTER_HEIGHT,
        )
        path.addRoundedRect(main_rect, self.CORNER_RADIUS, self.CORNER_RADIUS)

        pointer_top = main_rect.bottom()
        pointer_center_x = main_rect.center().x()
        pointer_path = QPainterPath()
        pointer_path.moveTo(pointer_center_x, pointer_top + self.POINTER_HEIGHT)
        pointer_path.lineTo(pointer_center_x - self.POINTER_WIDTH / 2.0, pointer_top)
        pointer_path.lineTo(pointer_center_x + self.POINTER_WIDTH / 2.0, pointer_top)
        pointer_path.closeSubpath()

        return path.united(pointer_path)

    def _create_placeholder(self) -> QPixmap:
        display_size = self.THUMBNAIL_DISPLAY_SIZE
        pixmap = QPixmap(display_size, display_size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(QColor("#cccccc"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, display_size, display_size, 8, 8)
        painter.end()
        return pixmap


class _GLMarkerLayer(_MarkerLayer):
    """Marker painter that renders inside the active GL map pass."""

    def __init__(self, target) -> None:
        super().__init__(None)
        self._target = target

    def update(self, *args, **kwargs) -> None:  # type: ignore[override]
        del args, kwargs
        self._target.request_full_update()


class PhotoMapView(QWidget):
    """Embed the map widget and manage geotagged photo markers."""

    assetActivated = Signal(str)
    """Signal emitted when the user activates a single asset marker."""

    clusterActivated = Signal(list)
    """Signal emitted when the user clicks a cluster with multiple assets.

    The payload is a list of :class:`GeotaggedAsset` objects representing the
    assets aggregated within the clicked cluster at the current zoom level.
    This enables O(1) gallery opening without additional database lookups.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        map_source: MapSourceSpec | None = None,
        map_runtime: MapRuntimePort | None = None,
        map_interaction_service: MapInteractionServicePort | None = None,
    ) -> None:
        super().__init__(parent)
        _configure_opaque_map_container(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._layout = layout
        self._requested_map_source = map_source
        self._map_runtime = map_runtime
        self._map_runtime_capabilities = (
            map_runtime.capabilities() if map_runtime is not None else None
        )
        self._map_interaction_service = (
            map_interaction_service or LibraryMapInteractionService()
        )
        self._map_package_root = resolve_map_package_root(map_runtime)
        self._map_widget: MapWidgetBase
        self._map_event_target: QWidget | None = None
        self._resolved_map_source: MapSourceSpec | None = None
        self._backend_kind = "unavailable"
        self._marker_paint_callback = None
        self._assets: list[GeotaggedAsset] = []
        self._assets_library_root: Path | None = None

        # ``FloatingToolTip`` replicates ``QToolTip`` using a styled ``QFrame``
        # instead of a custom paint routine.  The standard tooltip inherits the
        # translucent attributes from the frameless main window which causes the
        # popup to render as an opaque black rectangle on several window
        # managers.  Keeping a dedicated instance here ensures the tooltip
        # remains available for as long as the map view exists without fighting
        # Qt's global tooltip machinery.
        self._tooltip = FloatingToolTip()
        app = QApplication.instance()
        if app is not None:
            filter_candidate = app.property("floatingToolTipFilter")
            if isinstance(filter_candidate, ToolTipEventFilter):
                # The global filter already manages tooltips originating from
                # standard widgets.  Ignoring the map-specific tooltip prevents
                # the filter from hiding it prematurely when Qt dispatches
                # housekeeping events (for example ``Leave``) to the floating
                # popup itself.
                filter_candidate.ignore_object(self._tooltip)
        self._last_tooltip_text = ""
        self._thumbnail_loader = ThumbnailLoader(self)
        self._build_map_widget()

    def set_map_interaction_service(
        self,
        map_interaction_service: MapInteractionServicePort | None,
    ) -> None:
        """Bind the session-owned marker interaction rules."""

        self._map_interaction_service = (
            map_interaction_service or LibraryMapInteractionService()
        )

    @Slot(list)
    def _on_marker_activated(self, assets: list) -> None:
        """Route raw marker assets through the session interaction surface."""

        activation = self._map_interaction_service.activate_marker_assets(assets)
        if activation.kind == "asset" and activation.asset_relative:
            self.assetActivated.emit(activation.asset_relative)
        elif activation.kind == "cluster":
            self.clusterActivated.emit(list(activation.assets))

    def map_widget(self) -> MapWidgetBase:
        """Expose the underlying map widget for integration tests."""

        return self._map_widget

    def set_map_runtime(self, map_runtime: MapRuntimePort | None) -> None:
        """Bind the session-owned map runtime snapshot for later refreshes."""

        previous_capabilities = self._map_runtime_capabilities
        previous_package_root = self._map_package_root
        self._map_runtime = map_runtime
        self._map_runtime_capabilities = (
            map_runtime.capabilities() if map_runtime is not None else None
        )
        self._map_package_root = resolve_map_package_root(map_runtime)
        if (
            self._map_runtime_capabilities != previous_capabilities
            or self._map_package_root != previous_package_root
        ):
            self._rebuild_map_widget()

    def uses_native_osmand_widget(self) -> bool:
        """Return ``True`` when the current backend is the native GL widget."""

        return isinstance(self._map_widget, NativeOsmAndWidget)

    def runtime_diagnostics(self) -> str:
        """Return the last emitted runtime diagnostics line."""

        return self._runtime_diagnostics

    def set_assets(self, assets: Iterable[GeotaggedAsset], library_root: Path) -> None:
        """Replace the asset catalogue shown on the map."""

        self._assets = list(assets)
        self._assets_library_root = library_root
        self._marker_controller.set_assets(self._assets, library_root)

    def clear(self) -> None:
        """Remove all markers from the map."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        self._assets = []
        self._assets_library_root = None
        self._marker_controller.clear()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._overlay.parent() is self:
            self._overlay.setGeometry(self._map_widget.geometry())
        else:
            self._overlay.update()
        self._marker_controller.handle_resize()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        """Ensure the custom tooltip is dismissed when the view is hidden."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        super().hideEvent(event)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        """Clear hover feedback when the map relinquishes focus."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        super().focusOutEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self._map_event_target:
            if event.type() == QEvent.Type.MouseMove:
                mouse_event = cast(QMouseEvent, event)
                label = self._map_widget.city_at(mouse_event.position())
                if label:
                    global_pos = self._map_event_target.mapToGlobal(mouse_event.position().toPoint())
                    if label != self._last_tooltip_text:
                        # Refresh the popup only when the label changes to avoid
                        # flicker from repeatedly hiding and showing the widget
                        # as the cursor moves within the same city hit area.
                        self._tooltip.show_text(global_pos, label)
                        self._last_tooltip_text = label
                    else:
                        # The tooltip may need to be nudged when the cursor
                        # approaches the screen edge even if the underlying
                        # label stays the same, so keep it in sync with the
                        # current pointer location.
                        self._tooltip.show_text(global_pos, label)
                else:
                    if self._last_tooltip_text:
                        self._tooltip.hide_tooltip()
                        self._last_tooltip_text = ""
            elif event.type() == QEvent.Type.Leave:
                if self._last_tooltip_text:
                    self._tooltip.hide_tooltip()
                    self._last_tooltip_text = ""
            elif event.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonDblClick,
            ):
                mouse_event = cast(QMouseEvent, event)
                if self._last_tooltip_text:
                    self._tooltip.hide_tooltip()
                    self._last_tooltip_text = ""
                cluster = self._marker_controller.cluster_at(mouse_event.position())
                if cluster is not None:
                    self._marker_controller.handle_marker_click(cluster)
                    return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Ensure background workers shut down before the widget closes."""

        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        self._tooltip.hide_tooltip()
        self._tooltip.deleteLater()
        self._teardown_map_widget()
        super().closeEvent(event)

    def _handle_city_annotations(self, cities: Iterable[CityAnnotation]) -> None:
        """Forward city annotations to the map widget for background rendering."""

        self._map_widget.set_city_annotations(list(cities))

    def _build_map_widget(self) -> None:
        result = create_map_widget(
            self,
            map_source=self._requested_map_source,
            map_runtime_capabilities=self._map_runtime_capabilities,
            package_root=self._map_package_root,
            log=logger,
            context="photo map",
        )
        if result.widget is None:
            raise RuntimeError("Photo map widget backend unavailable")

        self._map_widget = result.widget
        self._backend_kind = result.backend_kind
        self._resolved_map_source = result.resolved_map_source
        assert self._resolved_map_source is not None
        actual_uses_gl = "confirmed_gl=true" in format_map_runtime_diagnostics(
            self._map_widget,
            backend_kind=result.backend_kind,
            map_source=self._resolved_map_source,
        )
        if result.backend_kind == "osmand_native":
            logger.info("Photo map initialised with the native OsmAnd OBF backend.")
        elif self._resolved_map_source.kind == "osmand_obf":
            if actual_uses_gl:
                logger.info("Photo map initialised with the OsmAnd OBF backend (GPU fallback).")
            else:
                logger.info("Photo map initialised with the OsmAnd OBF backend (CPU fallback).")
        elif actual_uses_gl:
            logger.info("Photo map initialised with GPU acceleration enabled.")
        elif result.use_opengl:
            logger.info("Photo map initialised with the legacy CPU map backend.")
        else:
            logger.info("Photo map using CPU rendering because OpenGL is unavailable.")
        if self._map_runtime_capabilities is not None:
            logger.info("Photo map runtime capability: %s", self._map_runtime_capabilities.status_message)
        self._layout.addWidget(self._map_widget)

        add_post_render_painter = getattr(self._map_widget, "add_post_render_painter", None)
        supports_post_render_painter = getattr(
            self._map_widget,
            "supports_post_render_painter",
            lambda: True,
        )
        if callable(add_post_render_painter) and supports_post_render_painter():
            self._overlay = _GLMarkerLayer(self._map_widget)
            self._marker_paint_callback = self._overlay.paint_markers
            add_post_render_painter(self._marker_paint_callback)
        else:
            self._overlay = _MarkerLayer(self)
            self._overlay.setGeometry(self._map_widget.geometry())
            self._overlay.raise_()

        self._map_event_target = cast(QWidget, self._map_widget.event_target())
        self._map_event_target.installEventFilter(self)
        self._runtime_diagnostics = format_map_runtime_diagnostics(
            self._map_widget,
            backend_kind=result.backend_kind,
            map_source=self._resolved_map_source,
        )
        logger.info(self._runtime_diagnostics)
        print(self._runtime_diagnostics, flush=True)

        self._marker_controller = MarkerController(
            self._map_widget,
            self._thumbnail_loader,
            marker_size=self._overlay.marker_size,
            thumbnail_size=self._overlay.thumbnail_size,
            provides_place_labels=self._map_widget.map_backend_metadata().provides_place_labels,
            parent=self,
        )

        self._map_widget.viewChanged.connect(self._marker_controller.handle_view_changed)
        self._map_widget.panned.connect(self._marker_controller.handle_pan)
        self._map_widget.panFinished.connect(self._marker_controller.handle_pan_finished)
        self._thumbnail_loader.ready.connect(self._marker_controller.handle_thumbnail_ready)
        self._marker_controller.clustersUpdated.connect(self._overlay.set_clusters)
        self._marker_controller.citiesUpdated.connect(self._handle_city_annotations)
        self._marker_controller.markerActivated.connect(self._on_marker_activated)
        self._marker_controller.thumbnailUpdated.connect(self._overlay.set_thumbnail)
        self._marker_controller.thumbnailsInvalidated.connect(self._overlay.clear_pixmaps)
        if self._assets_library_root is not None:
            self._marker_controller.set_assets(self._assets, self._assets_library_root)

    def _teardown_map_widget(self) -> None:
        if self._map_event_target is not None:
            self._map_event_target.removeEventFilter(self)
            self._map_event_target = None
        if self._marker_paint_callback is not None:
            remove_post_render_painter = getattr(self._map_widget, "remove_post_render_painter", None)
            if callable(remove_post_render_painter):
                remove_post_render_painter(self._marker_paint_callback)
            self._marker_paint_callback = None
        if hasattr(self, "_marker_controller"):
            # ``MarkerController`` maintains a worker thread that aggregates marker clusters.
            # Explicitly shutting it down prevents the Qt event loop from waiting indefinitely.
            self._marker_controller.shutdown()
            self._marker_controller.deleteLater()
        if hasattr(self, "_overlay"):
            self._overlay.hide()
            self._overlay.deleteLater()
        # The map widget owns a ``TileManager`` that runs in a separate ``QThread`` to
        # stream map tiles.  If the thread is not told to exit, the application process
        # keeps running after the window closes, so we must always shut it down here.
        self._layout.removeWidget(self._map_widget)
        self._map_widget.shutdown()
        self._map_widget.hide()
        self._map_widget.setParent(None)
        self._map_widget.deleteLater()

    def _rebuild_map_widget(self) -> None:
        if self._last_tooltip_text:
            self._tooltip.hide_tooltip()
            self._last_tooltip_text = ""
        if hasattr(self, "_map_widget"):
            self._teardown_map_widget()
        self._build_map_widget()


__all__ = ["PhotoMapView"]
