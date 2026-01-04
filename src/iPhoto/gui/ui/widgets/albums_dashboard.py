"""Dashboard view displaying all user albums."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import (
    QObject,
    QPoint,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....utils.pathutils import ensure_work_dir
from ....cache.index_store import IndexStore
from ....config import WORK_DIR_NAME
from ....media_classifier import get_media_type, MediaType
from ....models.album import Album
from ..tasks.thumbnail_loader import ThumbnailJob, generate_cache_path
from .flow_layout import FlowLayout
from ..icon import load_icon

if TYPE_CHECKING:
    from ....library.manager import LibraryManager
    from ....library.tree import AlbumNode


class RoundedImageView(QWidget):
    """Widget that draws a pixmap clipped to a rounded shape (left side only)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(80, 80)
        self._pixmap: QPixmap | None = None
        self._placeholder: QPixmap | None = None
        self._bg_color = QColor("#B0BEC5")

    def setPixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def setPlaceholder(self, pixmap: QPixmap) -> None:
        self._placeholder = pixmap
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        path = QPainterPath()
        r = 12.0
        w = float(self.width())
        h = float(self.height())

        # Draw shape: Left side rounded, right side straight
        path.moveTo(w, 0)
        path.lineTo(w, h)
        path.lineTo(r, h)
        path.quadTo(0, h, 0, h - r)
        path.lineTo(0, r)
        path.quadTo(0, 0, r, 0)
        path.closeSubpath()

        painter.setClipPath(path)

        if self._pixmap and not self._pixmap.isNull():
            # Scale cover to fill
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            # Background
            painter.fillPath(path, self._bg_color)
            # Placeholder icon centered
            if self._placeholder and not self._placeholder.isNull():
                px = (self.width() - self._placeholder.width()) // 2
                py = (self.height() - self._placeholder.height()) // 2
                painter.drawPixmap(px, py, self._placeholder)


class AlbumCard(QFrame):
    """Card widget representing a single album."""

    clicked = Signal(Path)

    def __init__(
        self,
        path: Path,
        title: str,
        count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.setMouseTracking(True)
        self._cursor_pos: QPoint | None = None

        # 1. Container dimensions
        self.setFixedSize(260, 80)
        self.setObjectName("AlbumCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # 2. Layout
        self.layout = QHBoxLayout(self)  # type: ignore[assignment]
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # 3. Left side: Image
        self.image_view = RoundedImageView(self)
        self.image_view.setObjectName("ImagePart")
        # Placeholder icon or text until image loads
        self.image_view.setPlaceholder(
            load_icon("photo.on.rectangle", color="#FFFFFF").pixmap(32, 32)
        )

        # 4. Right side: Metadata
        self.text_container = QWidget()
        self.text_container.setObjectName("TextPart")
        self.text_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.text_layout = QVBoxLayout(self.text_container)
        self.text_layout.setContentsMargins(15, 0, 10, 0)
        self.text_layout.setSpacing(4)
        self.text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Title
        self.title_label = QLabel()
        self.title_label.setStyleSheet(
            "color: #1d1d1f; font-size: 14px; font-weight: 600; background: transparent;"
        )
        self.set_title(title)

        # Count
        self.count_label = QLabel(str(count))
        self.count_label.setStyleSheet(
            "color: #86868b; font-size: 13px; background: transparent;"
        )

        self.text_layout.addWidget(self.title_label)
        self.text_layout.addWidget(self.count_label)

        self.layout.addWidget(self.image_view)
        self.layout.addWidget(self.text_container)

        # 5. Stylesheet
        # Note: Background color is handled in paintEvent for the light source effect
        self.setStyleSheet("""
            /* Parent container: rounded corners handled in paintEvent */
            #AlbumCard {
                border-radius: 12px;
            }

            /* Right text part: transparent */
            #TextPart {
                background-color: transparent;
            }
        """)

        # 6. Shadow
        self.add_shadow()

    def mouseMoveEvent(self, event) -> None:
        self._cursor_pos = event.position().toPoint()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._cursor_pos = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.path)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(self.rect(), 12, 12)

        if self._cursor_pos:
            # Highlight effect: Radial gradient from cursor
            # Center: White (#FFFFFF), Outer: #F5F5F7
            gradient = QRadialGradient(self._cursor_pos, 200)
            gradient.setColorAt(0.0, QColor("#FFFFFF"))
            gradient.setColorAt(1.0, QColor("#F5F5F7"))
            painter.fillPath(path, QBrush(gradient))
        else:
            # Default state
            painter.fillPath(path, QColor("#F5F5F7"))

    def add_shadow(self) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setXOffset(0)
        shadow.setYOffset(4)
        shadow.setColor(QColor(0, 0, 0, 25))
        self.setGraphicsEffect(shadow)

    def set_title(self, title: str) -> None:
        """Set the title with truncation if it exceeds 25 characters."""
        if len(title) > 25:
            truncated = title[:25] + "..."
            self.title_label.setText(truncated)
            self.title_label.setToolTip(title)
        else:
            self.title_label.setText(title)
            self.title_label.setToolTip("")

    def set_cover_image(self, pixmap: QPixmap) -> None:
        """Update the cover image."""
        self.image_view.setPixmap(pixmap)


class DashboardLoaderSignals(QObject):
    """Signals for the dashboard data loader."""

    albumReady = Signal(object, int, object, object, int)  # node, count, cover_path, album_root, generation


class AlbumDataWorker(QRunnable):
    """Background worker to fetch metadata (count, cover path) for an album."""

    def __init__(
        self,
        node: AlbumNode,
        signals: DashboardLoaderSignals,
        generation: int,
        library_root: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.node = node
        self.signals = signals
        self.generation = generation
        self._library_root = library_root

    def run(self) -> None:
        # 1. Get count and first asset for cover fallback
        count = 0
        first_rel: str | None = None

        try:
            # Use library root for global database if available
            index_root = self._library_root if self._library_root else self.node.path
            store = IndexStore(index_root)
            
            # Compute album path for filtering
            album_path: Optional[str] = None
            if self._library_root:
                try:
                    node_resolved = self.node.path.resolve()
                    lib_resolved = self._library_root.resolve()
                    if node_resolved != lib_resolved:
                        album_path = node_resolved.relative_to(lib_resolved).as_posix()
                except (ValueError, OSError):
                    # If we cannot resolve or relativize paths (e.g. outside library root or
                    # due to filesystem issues), fall back to using the full index without
                    # an album-specific filter.
                    pass

            # Count assets for this album using the count method with album filter
            count = store.count_album_assets(album_path, include_subalbums=True) if album_path else store.count()
            
            # Get first asset for cover fallback
            for row in store.read_album_assets(album_path) if album_path else store.read_all():
                if isinstance(row, dict):
                    rel = row.get("rel", "")
                    if isinstance(rel, str) and rel:
                        # If using album_path filter, rel is library-relative.
                        # Ensure first_rel is always album-relative when joined with self.node.path.
                        if album_path:
                            prefix = album_path.rstrip("/") + "/"
                            if rel.startswith(prefix):
                                inner = rel[len(prefix):]
                                if inner:
                                    first_rel = inner
                                    break
                        else:
                            # When there is no album_path (library root), rel is already correct.
                            first_rel = rel
                            break
        except Exception:
            pass

        # 2. Determine cover path
        cover_path: Path | None = None
        try:
            album = Album.open(self.node.path)
            cover_rel = album.manifest.get("cover")
            if cover_rel:
                candidate = self.node.path / cover_rel
                if candidate.exists():
                    cover_path = candidate
        except Exception:
            pass

        if cover_path is None and first_rel:
            candidate = self.node.path / first_rel
            if candidate.exists():
                cover_path = candidate

        self.signals.albumReady.emit(self.node, count, cover_path, self.node.path, self.generation)


class DashboardThumbnailLoader(QObject):
    """Simplified thumbnail loader for dashboard cards."""

    thumbnailReady = Signal(Path, QPixmap)  # album_root, pixmap
    _delivered = Signal(str, QImage, str)  # key, image, rel

    def __init__(self, parent: QObject | None = None, library_root: Optional[Path] = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._delivered.connect(self._handle_result)
        # Map unique keys to album roots
        self._key_to_root: dict[str, Path] = {}
        self._library_root = library_root

    def request_with_absolute_key(self, album_root: Path, image_path: Path, size: QSize) -> None:
        # To avoid rel collision across albums, we use the absolute path string as the 'rel' identifier
        # passed to ThumbnailJob. This ensures the key emitted back is unique.
        unique_rel = str(image_path)

        # Use library root if available, otherwise fallback to album root
        effective_library_root = self._library_root if self._library_root else album_root

        try:
            work_dir = ensure_work_dir(effective_library_root, WORK_DIR_NAME)
            thumbs_dir = work_dir / "thumbs"
            thumbs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        try:
            stat = image_path.stat()
        except OSError:
            return
        stamp = int(stat.st_mtime * 1_000_000_000)

        # Use standardized generator with absolute path
        cache_path = generate_cache_path(effective_library_root, image_path, size, stamp)

        if cache_path.exists():
            pixmap = QPixmap(str(cache_path))
            if not pixmap.isNull():
                self.thumbnailReady.emit(album_root, pixmap)
                return

        # Store mapping
        key_str = self._make_key_str(unique_rel, size, stamp)
        self._key_to_root[key_str] = album_root

        media_type = get_media_type(image_path)
        is_image = media_type == MediaType.IMAGE
        is_video = media_type == MediaType.VIDEO

        # Determine cache_rel based on library root if possible to match main loader behavior,
        # but DashboardThumbnailLoader logic uses unique_rel as the key.
        # We pass effective_library_root as library_root to ThumbnailJob.

        job = ThumbnailJob(
            self,  # type: ignore
            unique_rel,  # Pass absolute path string as rel to ensure uniqueness
            image_path,
            size,
            None,  # Pass None as known_stamp to force regeneration if missing
            album_root,
            effective_library_root,
            is_image=is_image,
            is_video=is_video,
            still_image_time=None,
            duration=None,
            cache_rel=None, # Not used when hashing absolute path in new logic?
            # Wait, ThumbnailJob still uses _cache_rel if provided?
            # In new logic: rel_for_path = self._cache_rel if self._cache_rel is not None else self._rel
            # Then: generate_cache_path(self._library_root, self._abs_path, ...)
            # generate_cache_path IGNORES rel/cache_rel now! It uses abs_path.
            # So cache_rel is irrelevant for path generation, but might be used for logging?
            # The job passes it. Let's pass None or keep it consistent?
            # The old code calculated real_rel.
            # Let's pass None as it's not needed for the path generation anymore.
        )
        self._pool.start(job)

    def _make_key(self, rel: str, size: QSize, stamp: int) -> str:
        # Used by ThumbnailJob to emit signal
        return self._make_key_str(rel, size, stamp)

    def _make_key_str(self, rel: str, size: QSize, stamp: int) -> str:
        return f"{rel}::{size.width()}::{size.height()}::{stamp}"

    def _handle_result(self, key: str, image: Optional[QImage], rel: str) -> None:
        album_root = self._key_to_root.pop(key, None)
        if not album_root or image is None:
            return

        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            self.thumbnailReady.emit(album_root, pixmap)


class AlbumsDashboard(QWidget):
    """Main view for browsing all user albums."""

    albumSelected = Signal(Path)

    def __init__(self, library: LibraryManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._cards: dict[Path, AlbumCard] = {}
        # Track refresh generation to prevent race conditions
        # Python integers can grow arbitrarily large, so overflow is not a concern
        self._current_generation = 0

        # Setup loader
        self._loader_signals = DashboardLoaderSignals()
        self._loader_signals.albumReady.connect(self._on_album_data_ready)

        self._thumb_loader = DashboardThumbnailLoader(self, library_root=self._library.root())
        self._thumb_loader.thumbnailReady.connect(self._on_thumbnail_ready)

        self._init_ui()
        self._library.treeUpdated.connect(self.refresh)
        self.refresh()

    def _init_ui(self) -> None:
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(40, 40, 40, 40)
        self.main_layout.setSpacing(20)

        # Header
        self.header_label = QLabel("Albums")
        font = QFont()
        font.setPixelSize(22)
        font.setBold(True)
        self.header_label.setFont(font)
        self.header_label.setStyleSheet("color: #1d1d1f; margin-bottom: 10px;")
        self.main_layout.addWidget(self.header_label)

        # Scroll Area for the grid
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("background: transparent;")

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.flow_layout = FlowLayout(self.scroll_content, margin=0, h_spacing=20, v_spacing=20)

        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)

        # Empty state placeholder
        self.empty_label = QLabel(self.tr("No albums available"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #86868b; font-size: 16px;")
        self.empty_label.hide()
        self.main_layout.addWidget(self.empty_label)

    def refresh(self) -> None:
        # Increment generation to invalidate pending workers from previous refresh
        self._current_generation += 1
        # Clear existing
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._cards.clear()

        albums = self._library.list_albums()

        if not albums:
            self.scroll_area.hide()
            self.empty_label.show()
            return

        self.empty_label.hide()
        self.scroll_area.show()

        pool = QThreadPool.globalInstance()
        current_gen = self._current_generation
        library_root = self._library.root()

        for album in albums:
            # Create card with "0" count first
            card = AlbumCard(album.path, album.title, 0, self.scroll_content)
            card.clicked.connect(self.albumSelected)
            self.flow_layout.addWidget(card)
            self._cards[album.path] = card

            # Fetch data with current generation, using library root for global DB
            worker = AlbumDataWorker(album, self._loader_signals, current_gen, library_root=library_root)
            pool.start(worker)

    def _on_album_data_ready(
        self, node: AlbumNode, count: int, cover_path: Path | None, root: Path, generation: int
    ) -> None:
        # Ignore results from outdated refresh operations
        if generation != self._current_generation:
            return
        card = self._cards.get(root)
        if not card:
            return

        # Update count
        card.count_label.setText(str(count))

        # Load cover
        if cover_path:
            self._thumb_loader.request_with_absolute_key(root, cover_path, QSize(512, 512))

    def _on_thumbnail_ready(self, album_root: Path, pixmap: QPixmap) -> None:
        card = self._cards.get(album_root)
        if card:
            card.set_cover_image(pixmap)
