"""Shared constants and helpers for the People dashboard widgets."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QWidget

from iPhoto.config import WORK_DIR_NAME
from iPhoto.infrastructure.services.people_cover_cache_service import PeopleCoverCacheService
from iPhoto.people.image_utils import create_cover_thumbnail
from iPhoto.utils.pathutils import ensure_work_dir

CARD_WIDTH = 156
CARD_HEIGHT = 212
CARD_RADIUS = 24
GROUP_CARD_WIDTH = 292
GROUP_CARD_HEIGHT = 178
GROUP_CARD_RADIUS = 24
AVATAR_TILE_WIDTH = 148
AVATAR_TILE_HEIGHT = 154
AVATAR_SIZE = 94
SPACING = 18
PROXIMITY_THRESHOLD = 120
CANVAS_MARGIN = 18
PLACEHOLDER_BACKDROPS: tuple[tuple[str, str], ...] = (
    ("#5A7C6A", "#20352C"),
    ("#A54C53", "#3C2024"),
    ("#C69B6E", "#6A4427"),
    ("#668B6E", "#25352B"),
    ("#5D677A", "#232A35"),
    ("#A9B8C9", "#415166"),
)

MENU_STYLE = """
QMenu {
    background-color: #FFFFFF;
    border: 1px solid rgba(17, 24, 39, 0.12);
    border-radius: 14px;
    padding: 8px;
}
QMenu::item {
    background-color: transparent;
    color: #111827;
    padding: 8px 18px;
    border-radius: 10px;
}
QMenu::item:selected {
    background-color: #EAF2FF;
    color: #1D4ED8;
}
QMenu::separator {
    height: 1px;
    background: rgba(17, 24, 39, 0.10);
    margin: 6px 10px;
}
"""

_PEOPLE_COVER_CACHE = PeopleCoverCacheService(Path.home() / WORK_DIR_NAME / "cache" / "people-covers")


def _qcolor(value: str | QColor, alpha: int | None = None) -> QColor:
    color = QColor(value) if not isinstance(value, QColor) else QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def _rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _create_pos_anim(widget: QWidget, target: QPoint, duration: int = 240) -> QPropertyAnimation:
    anim = QPropertyAnimation(widget, b"pos")
    anim.setDuration(duration)
    anim.setStartValue(widget.pos())
    anim.setEndValue(target)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    return anim


def configure_people_cover_cache(library_root: Path | None) -> None:
    if library_root is None:
        cache_root = Path.home() / WORK_DIR_NAME / "cache" / "people-covers"
    else:
        cache_root = ensure_work_dir(library_root) / "cache" / "people-covers"
    _PEOPLE_COVER_CACHE.set_disk_cache_path(cache_root)


def people_cover_cache() -> PeopleCoverCacheService:
    return _PEOPLE_COVER_CACHE


def request_cover_pixmap(image_path: Path | None, size: tuple[int, int]) -> tuple[str | None, QPixmap | None]:
    if image_path is None:
        return None, None
    return _PEOPLE_COVER_CACHE.get_thumbnail(image_path, size)


def request_rendered_cover_pixmap(
    *,
    cache_id: str,
    signature_parts: Iterable[str],
    size: tuple[int, int],
    renderer,
) -> tuple[str | None, QPixmap | None]:
    signature = hashlib.md5("\x00".join(signature_parts).encode("utf-8")).hexdigest()
    return _PEOPLE_COVER_CACHE.get_rendered_cover(
        cache_id=cache_id,
        size=size,
        signature=signature,
        renderer=renderer,
    )


def _pixmap_from_image_path(image_path: Path, size: tuple[int, int]) -> QPixmap | None:
    _cache_key, pixmap = request_cover_pixmap(image_path, size)
    return pixmap


def qimage_from_cover_image(image, size: tuple[int, int]) -> QImage:
    width, height = int(size[0]), int(size[1])
    cover = create_cover_thumbnail(image, (width, height))
    data = cover.tobytes("raw", "RGBA")
    return QImage(
        data,
        width,
        height,
        width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()


def _widget_uses_dark_theme(widget: QWidget | None) -> bool:
    if widget is None:
        return False
    return widget.palette().color(QPalette.ColorRole.Window).lightness() < 128


def _resolve_dashboard_dark_mode(parent: QWidget | None) -> bool:
    """Resolve the effective dashboard theme from context, then the painted palette."""

    widget = parent.window() if parent is not None and parent.window() is not None else parent
    coordinator = getattr(widget, "coordinator", None)
    context = getattr(coordinator, "_context", None)
    theme_manager = getattr(context, "theme", None)
    if theme_manager is not None:
        effective_mode = getattr(theme_manager, "get_effective_theme_mode", None)
        if callable(effective_mode):
            return effective_mode() == "dark"
        current_colors = getattr(theme_manager, "current_colors", None)
        if callable(current_colors):
            colors = current_colors()
            is_dark = getattr(colors, "is_dark", None)
            if isinstance(is_dark, bool):
                return is_dark

    settings = getattr(context, "settings", None)
    if settings is not None and hasattr(settings, "get"):
        theme_setting = settings.get("ui.theme", "system")
        if theme_setting == "dark":
            return True
        if theme_setting == "light":
            return False

    # The application palette is the visual source of truth after an explicit
    # Light/Dark selection. It must win over the OS scheme (for example, Light
    # Mode inside a macOS session whose system appearance is dark).
    if widget is not None:
        return _widget_uses_dark_theme(widget)
    if parent is not None:
        return _widget_uses_dark_theme(parent)
    app = QApplication.instance()
    if app is not None:
        return app.palette().color(QPalette.ColorRole.Window).lightness() < 128
    return False


def _button_distance(first: QWidget, second: QWidget) -> float:
    c1 = first.pos() + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2)
    c2 = second.pos() + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2)
    return math.hypot(c1.x() - c2.x(), c1.y() - c2.y())


class HintFrame(QFrame):
    def __init__(self, parent: QWidget, style_sheet: str) -> None:
        super().__init__(parent)
        self.setStyleSheet(style_sheet)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
