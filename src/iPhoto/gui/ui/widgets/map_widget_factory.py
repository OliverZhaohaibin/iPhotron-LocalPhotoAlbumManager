"""Shared Qt map widget construction for full-size and compact map views."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from logging import Logger, getLogger
from pathlib import Path

from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
from PySide6.QtWidgets import QWidget

from ....application.ports import MapRuntimeCapabilities, MapRuntimePort
from maps.map_sources import (
    MapSourceSpec,
    has_usable_osmand_default,
    has_usable_osmand_native_widget,
    prefer_osmand_native_widget,
)
from maps.map_widget._map_widget_base import MapWidgetBase
from maps.map_widget.map_gl_widget import MapGLWidget, MapGLWindowWidget
from maps.map_widget.map_widget import MapWidget
from maps.map_widget.native_osmand_widget import (
    NativeOsmAndWidget,
    probe_native_widget_runtime,
)
from maps.map_widget.qt_location_map_widget import QtLocationMapWidget


logger = getLogger(__name__)
_MAPS_PACKAGE_ROOT = Path(__file__).resolve().parents[4] / "maps"


@dataclass(slots=True)
class MapWidgetFactoryResult:
    widget: MapWidgetBase | None
    resolved_map_source: MapSourceSpec | None
    backend_kind: str
    use_opengl: bool


def _opengl_explicitly_disabled() -> bool:
    return os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _native_widget_runtime_is_usable() -> bool:
    return _native_widget_runtime_is_usable_for_root(_MAPS_PACKAGE_ROOT)


def _native_widget_runtime_is_usable_for_root(package_root: Path) -> bool:
    if not has_usable_osmand_native_widget(package_root):
        return False

    is_available, reason = probe_native_widget_runtime(package_root)
    if not is_available and reason:
        logger.warning("Native OsmAnd widget runtime probe failed: %s", reason)
    return is_available


def check_opengl_support() -> bool:
    """Return ``True`` when the system can create a basic OpenGL context."""

    if _opengl_explicitly_disabled():
        return False

    strict_probe = sys.platform == "darwin"
    try:
        surface = QOffscreenSurface()
        surface.create()

        context = QOpenGLContext()
        if not context.create():
            return False

        if hasattr(context, "isValid") and not context.isValid():
            return False

        if not surface.isValid():
            return not strict_probe
        if not context.makeCurrent(surface):
            return not strict_probe
        try:
            if strict_probe:
                functions = context.functions()
                if functions is None:
                    return False
                version = functions.glGetString(0x1F02)
                if not version:
                    return False
        finally:
            context.doneCurrent()
        return True
    except Exception:  # noqa: BLE001 - fall back gracefully on any Qt failure
        return False


def _resolve_map_source(
    map_source: MapSourceSpec,
    package_root: Path = _MAPS_PACKAGE_ROOT,
) -> MapSourceSpec:
    return map_source.resolved(package_root)


def _has_resolved_osmand_assets(map_source: MapSourceSpec) -> bool:
    if map_source.kind != "osmand_obf":
        return False

    return (
        Path(map_source.data_path).exists()
        and Path(map_source.resources_root or "").exists()
        and Path(map_source.style_path or "").exists()
    )


def _preferred_python_widget_class(*, use_opengl: bool) -> type[MapWidgetBase]:
    if not use_opengl:
        return MapWidget
    if sys.platform == "darwin":
        return MapGLWindowWidget
    return MapGLWidget


def choose_map_widget_backend(
    map_source: MapSourceSpec | None,
    *,
    use_opengl: bool,
    runtime_capabilities: MapRuntimeCapabilities | None = None,
    package_root: Path = _MAPS_PACKAGE_ROOT,
) -> tuple[type[MapWidgetBase], MapSourceSpec | None, str]:
    """Return the preferred widget class and source for map views."""

    python_widget_cls = _preferred_python_widget_class(use_opengl=use_opengl)
    native_widget_usable = (
        runtime_capabilities.native_widget_available
        if runtime_capabilities is not None
        else (
            not _opengl_explicitly_disabled()
            and prefer_osmand_native_widget()
            and _native_widget_runtime_is_usable_for_root(package_root)
        )
    )

    if map_source is not None:
        resolved_map_source = _resolve_map_source(map_source, package_root)
        if resolved_map_source.kind == "osmand_obf":
            if native_widget_usable:
                return NativeOsmAndWidget, resolved_map_source, "osmand_native"
            return python_widget_cls, resolved_map_source, "osmand_python"

        return python_widget_cls, resolved_map_source, "legacy_python"

    default_osmand_source = MapSourceSpec.osmand_default(package_root).resolved(package_root)
    osmand_assets_available = (
        runtime_capabilities.osmand_extension_available
        if runtime_capabilities is not None
        else _has_resolved_osmand_assets(default_osmand_source)
    )
    if osmand_assets_available:
        if native_widget_usable:
            return NativeOsmAndWidget, default_osmand_source, "osmand_native"
        if runtime_capabilities is not None or has_usable_osmand_default(package_root):
            return python_widget_cls, default_osmand_source, "osmand_python"

    legacy_source = MapSourceSpec.legacy_default(package_root).resolved(package_root)
    return python_widget_cls, legacy_source, "legacy_python"


def _choose_map_widget_backend_with_runtime(
    map_source: MapSourceSpec | None,
    *,
    use_opengl: bool,
    runtime_capabilities: MapRuntimeCapabilities,
    package_root: Path = _MAPS_PACKAGE_ROOT,
) -> tuple[type[MapWidgetBase], MapSourceSpec | None, str]:
    try:
        return choose_map_widget_backend(
            map_source,
            use_opengl=use_opengl,
            runtime_capabilities=runtime_capabilities,
            package_root=package_root,
        )
    except TypeError as exc:
        if "runtime_capabilities" not in str(exc) and "package_root" not in str(exc):
            raise
        try:
            return choose_map_widget_backend(
                map_source,
                use_opengl=use_opengl,
                runtime_capabilities=runtime_capabilities,
            )
        except TypeError as inner_exc:
            if "runtime_capabilities" not in str(inner_exc):
                raise
            return choose_map_widget_backend(map_source, use_opengl=use_opengl)


def _choose_map_widget_backend_for_root(
    map_source: MapSourceSpec | None,
    *,
    use_opengl: bool,
    package_root: Path,
) -> tuple[type[MapWidgetBase], MapSourceSpec | None, str]:
    try:
        return choose_map_widget_backend(
            map_source,
            use_opengl=use_opengl,
            package_root=package_root,
        )
    except TypeError as exc:
        if "package_root" not in str(exc):
            raise
        return choose_map_widget_backend(map_source, use_opengl=use_opengl)


def resolve_map_package_root(map_runtime: MapRuntimePort | None) -> Path:
    package_root_getter = getattr(map_runtime, "package_root", None)
    if callable(package_root_getter):
        try:
            package_root = package_root_getter()
        except Exception:
            logger.debug("Failed to resolve map package root", exc_info=True)
        else:
            if package_root is not None:
                return Path(package_root).resolve()

    package_root = getattr(map_runtime, "_package_root", None)
    if package_root is not None:
        return Path(package_root).resolve()
    return _MAPS_PACKAGE_ROOT.resolve()


def _confirmed_gl_state(
    map_widget: MapWidgetBase,
    *,
    backend_kind: str,
) -> str:
    if backend_kind == "osmand_native":
        return "true"
    if isinstance(map_widget, (MapGLWidget, MapGLWindowWidget)):
        return "true"
    if isinstance(map_widget, MapWidget):
        return "false"
    if isinstance(map_widget, QtLocationMapWidget):
        return "unknown"
    return "unknown"


def format_map_runtime_diagnostics(
    map_widget: MapWidgetBase,
    *,
    backend_kind: str,
    map_source: MapSourceSpec | None,
) -> str:
    """Return a one-line runtime summary that proves whether GL is active."""

    source_kind = map_source.kind if map_source is not None else "none"
    metadata = map_widget.map_backend_metadata()
    event_target = map_widget.event_target()
    event_target_name = getattr(event_target, "objectName", lambda: "")()
    if not event_target_name:
        event_target_name = type(event_target).__name__
    native_library_path = getattr(map_widget, "loaded_library_path", lambda: None)()
    native_library_suffix = ""
    if native_library_path:
        native_library_suffix = f" native_dll={native_library_path}"

    return (
        "[PhotoMapView] "
        f"backend={backend_kind} "
        f"confirmed_gl={_confirmed_gl_state(map_widget, backend_kind=backend_kind)} "
        f"widget={type(map_widget).__name__} "
        f"event_target={event_target_name} "
        f"source={source_kind} "
        f"tile_kind={metadata.tile_kind} "
        f"tile_scheme={metadata.tile_scheme}"
        f"{native_library_suffix}"
    )


def create_map_widget(
    parent: QWidget,
    *,
    map_source: MapSourceSpec | None,
    map_runtime_capabilities: MapRuntimeCapabilities | None,
    package_root: Path,
    log: Logger | None = None,
    context: str = "map",
) -> MapWidgetFactoryResult:
    """Build a map widget and apply the shared native/GL/CPU fallback policy."""

    active_logger = log or logger
    if map_runtime_capabilities is not None:
        use_opengl = map_runtime_capabilities.python_gl_available
        widget_cls, resolved_map_source, backend_kind = _choose_map_widget_backend_with_runtime(
            map_source,
            use_opengl=use_opengl,
            runtime_capabilities=map_runtime_capabilities,
            package_root=package_root,
        )
    else:
        use_opengl = check_opengl_support()
        widget_cls, resolved_map_source, backend_kind = _choose_map_widget_backend_for_root(
            map_source,
            use_opengl=use_opengl,
            package_root=package_root,
        )

    assert resolved_map_source is not None
    try:
        widget = widget_cls(parent, map_source=resolved_map_source)
        return MapWidgetFactoryResult(widget, resolved_map_source, backend_kind, use_opengl)
    except Exception as exc:
        if backend_kind == "osmand_native":
            active_logger.warning(
                "Native OsmAnd widget unavailable for %s, falling back: %s",
                context,
                exc,
            )
            fallback_cls = _preferred_python_widget_class(use_opengl=use_opengl)
            try:
                widget = fallback_cls(parent, map_source=resolved_map_source)
                return MapWidgetFactoryResult(
                    widget,
                    resolved_map_source,
                    "osmand_python",
                    use_opengl,
                )
            except Exception as fallback_exc:
                if not use_opengl:
                    raise
                active_logger.warning(
                    "OpenGL OBF fallback unavailable for %s, falling back to CPU: %s",
                    context,
                    fallback_exc,
                )
                widget = MapWidget(parent, map_source=resolved_map_source)
                return MapWidgetFactoryResult(
                    widget,
                    resolved_map_source,
                    "osmand_python",
                    use_opengl,
                )
        if widget_cls in {MapGLWidget, MapGLWindowWidget}:
            active_logger.warning(
                "OpenGL map unavailable for %s, falling back to CPU renderer: %s",
                context,
                exc,
            )
            widget = MapWidget(parent, map_source=resolved_map_source)
            fallback_backend_kind = (
                "osmand_python"
                if resolved_map_source.kind == "osmand_obf"
                else "legacy_python"
            )
            return MapWidgetFactoryResult(
                widget,
                resolved_map_source,
                fallback_backend_kind,
                use_opengl,
            )
        active_logger.warning("%s backend unavailable", context, exc_info=True)
        return MapWidgetFactoryResult(None, resolved_map_source, "unavailable", use_opengl)


__all__ = [
    "MapGLWidget",
    "MapGLWindowWidget",
    "MapWidget",
    "MapWidgetBase",
    "MapWidgetFactoryResult",
    "MapSourceSpec",
    "NativeOsmAndWidget",
    "QtLocationMapWidget",
    "_MAPS_PACKAGE_ROOT",
    "_choose_map_widget_backend_for_root",
    "_choose_map_widget_backend_with_runtime",
    "_confirmed_gl_state",
    "_native_widget_runtime_is_usable",
    "_native_widget_runtime_is_usable_for_root",
    "_opengl_explicitly_disabled",
    "_preferred_python_widget_class",
    "check_opengl_support",
    "choose_map_widget_backend",
    "create_map_widget",
    "format_map_runtime_diagnostics",
    "resolve_map_package_root",
]
