"""Caching helpers for :class:`AssetListModel`."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QSize, Signal, Qt, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap, QImage

from ..tasks.thumbnail_loader import ThumbnailLoader
from ..geometry_utils import calculate_center_crop


class AssetCacheManager(QObject):
    """Manage thumbnail and placeholder caches."""

    thumbnailReady = Signal(Path, str, QPixmap)

    def __init__(self, thumb_size: QSize, parent: QObject | None = None, library_root: Optional[Path] = None) -> None:
        """Create an empty cache for thumbnails and transient metadata."""

        super().__init__(parent)
        self._thumb_size = QSize(thumb_size)
        self._thumb_loader = ThumbnailLoader(self, library_root=library_root)
        self._thumb_loader.ready.connect(self._on_thumb_ready)
        self._thumb_cache: Dict[str, QPixmap] = {}
        self._composite_cache: Dict[str, QPixmap] = {}
        self._placeholder_cache: Dict[str, QPixmap] = {}
        self._placeholder_templates: Dict[str, QPixmap] = {}
        self._recently_removed_rows: "OrderedDict[str, Dict[str, object]]" = OrderedDict()
        self._recently_removed_limit = 256
        self._album_root: Optional[Path] = None

    def thumbnail_loader(self) -> ThumbnailLoader:
        """Expose the :class:`ThumbnailLoader` used for rendering previews."""

        return self._thumb_loader

    def set_library_root(self, root: Path) -> None:
        """Update the shared library root for centralized thumbnail storage."""
        self._thumb_loader.set_library_root(root)

    def reset_for_album(self, root: Optional[Path]) -> None:
        """Reset caches so a new album can be loaded."""

        self._album_root = root
        self._thumb_loader.reset_for_album(root)
        self._thumb_cache.clear()
        self._composite_cache.clear()
        self._placeholder_cache.clear()
        self._placeholder_templates.clear()
        self._recently_removed_rows.clear()

    def clear_recently_removed(self) -> None:
        """Drop all cached metadata for recently removed rows."""

        self._recently_removed_rows.clear()

    def reset_caches_for_new_rows(self, rows: List[Dict[str, object]]) -> None:
        """Synchronise transient caches with the freshly loaded *rows*.

        The asset grid keeps placeholder, thumbnail and "recently removed"
        caches alongside the authoritative dataset maintained by
        :class:`AssetListStateManager`.  When the model performs a synchronous
        reload from the index cache we must evict stale entries that belong to
        the previous snapshot; otherwise thumbnails for assets that no longer
        exist would linger in memory and optimistic removal snapshots would
        never be cleared.
        """

        active_rel_keys: set[str] = set()
        for row in rows:
            rel_value = str(row.get("rel", "") or "")
            if rel_value:
                active_rel_keys.add(rel_value)
            abs_value = row.get("abs")
            if abs_value:
                self.remove_recently_removed(str(abs_value))

        if not active_rel_keys:
            self.clear_all_thumbnails()
            self.clear_placeholders()
            return

        self.clear_thumbnails_not_in(active_rel_keys)
        self._composite_cache = {rel: pix for rel, pix in self._composite_cache.items() if rel in active_rel_keys}
        # Placeholders are light-weight so we rebuild them lazily, but removing
        # stale entries keeps the cache keyed to the active dataset and avoids
        # accidentally returning templates for rows that no longer exist.
        obsolete_placeholders = set(self._placeholder_cache.keys()) - active_rel_keys
        for rel_key in obsolete_placeholders:
            self._placeholder_cache.pop(rel_key, None)

    def set_recently_removed_limit(self, limit: int) -> None:
        """Set the maximum number of cached removal snapshots."""

        self._recently_removed_limit = max(limit, 0)

    def recently_removed(self, absolute_key: str) -> Optional[Dict[str, object]]:
        """Return cached metadata for *absolute_key* if present."""

        return self._recently_removed_rows.get(absolute_key)

    def stash_recently_removed(self, absolute_key: str, metadata: Dict[str, object]) -> None:
        """Store a metadata snapshot for a row that was removed optimistically."""

        self._recently_removed_rows[absolute_key] = dict(metadata)
        self._recently_removed_rows.move_to_end(absolute_key)
        while len(self._recently_removed_rows) > self._recently_removed_limit:
            self._recently_removed_rows.popitem(last=False)

    def remove_recently_removed(self, absolute_key: str) -> None:
        """Discard cached metadata for *absolute_key* if it exists."""

        self._recently_removed_rows.pop(absolute_key, None)

    def thumbnail_for(self, rel: str) -> Optional[QPixmap]:
        """Return the cached thumbnail for *rel*, if available."""

        return self._thumb_cache.get(rel)

    def set_thumbnail(self, rel: str, pixmap: QPixmap) -> None:
        """Store *pixmap* under the cache key *rel*."""

        self._thumb_cache[rel] = pixmap
        # Invalidate the composite cache entry for this key, since the composite
        # must be regenerated from the new thumbnail data.
        self._composite_cache.pop(rel, None)

    def remove_thumbnail(self, rel: str) -> None:
        """Remove the cached thumbnail for *rel* when it exists."""

        self._thumb_cache.pop(rel, None)
        self._composite_cache.pop(rel, None)

    def move_thumbnail(self, old_rel: str, new_rel: str) -> None:
        """Move the cached thumbnail from *old_rel* to *new_rel*."""

        pixmap = self._thumb_cache.pop(old_rel, None)
        if pixmap is not None:
            self._thumb_cache[new_rel] = pixmap

        comp_pixmap = self._composite_cache.pop(old_rel, None)
        if comp_pixmap is not None:
            self._composite_cache[new_rel] = comp_pixmap

    def clear_thumbnails_not_in(self, active: set[str]) -> None:
        """Discard cached thumbnails whose keys are not present in *active*."""

        self._thumb_cache = {rel: pix for rel, pix in self._thumb_cache.items() if rel in active}

    def clear_all_thumbnails(self) -> None:
        """Remove every cached thumbnail."""

        self._thumb_cache.clear()
        self._composite_cache.clear()

    def move_placeholder(self, old_rel: str, new_rel: str) -> None:
        """Move the cached placeholder entry from *old_rel* to *new_rel*."""

        placeholder = self._placeholder_cache.pop(old_rel, None)
        if placeholder is not None:
            self._placeholder_cache[new_rel] = placeholder

    def remove_placeholder(self, rel: str) -> None:
        """Remove the cached placeholder associated with *rel*."""

        self._placeholder_cache.pop(rel, None)

    def clear_placeholders(self) -> None:
        """Erase all cached placeholders so they will be regenerated lazily."""

        self._placeholder_cache.clear()

    def resolve_thumbnail(
        self,
        row: Dict[str, object],
        priority: ThumbnailLoader.Priority,
    ) -> QPixmap:
        """Return a thumbnail for *row*, requesting it asynchronously if needed."""

        rel = str(row["rel"])

        # Check for pre-composed thumbnail first
        composite = self._composite_cache.get(rel)
        if composite is not None:
            return composite

        cached = self._thumb_cache.get(rel)
        if cached is not None:
            # Generate composite from cached raw thumbnail
            return self._create_composite_thumbnail(rel, cached, row)

        placeholder = self._placeholder_for(rel, bool(row.get("is_video")))
        if not self._album_root:
            return placeholder

        abs_value = row.get("abs", "")
        abs_path = Path(str(abs_value)) if abs_value else self._album_root / rel

        # Trigger async load even if we return a micro thumbnail
        if bool(row.get("is_image")):
            pixmap = self._thumb_loader.request(
                rel,
                abs_path,
                self._thumb_size,
                is_image=True,
                priority=priority,
            )
            if pixmap is not None:
                self._thumb_cache[rel] = pixmap
                return self._create_composite_thumbnail(rel, pixmap, row)

        if bool(row.get("is_video")):
            still_time = row.get("still_image_time")
            duration = row.get("dur")
            still_hint: Optional[float] = float(still_time) if isinstance(still_time, (int, float)) else None
            duration_value: Optional[float] = float(duration) if isinstance(duration, (int, float)) else None
            if still_hint is not None and duration_value and duration_value > 0:
                max_seek = max(duration_value - 0.01, 0.0)
                if still_hint > max_seek:
                    still_hint = max_seek
            pixmap = self._thumb_loader.request(
                rel,
                abs_path,
                self._thumb_size,
                is_image=False,
                is_video=True,
                still_image_time=still_hint,
                duration=duration_value,
                priority=priority,
            )
            if pixmap is not None:
                self._thumb_cache[rel] = pixmap
                return self._create_composite_thumbnail(rel, pixmap, row)

        # Check for micro thumbnail before falling back to generic placeholder
        micro_thumb = row.get("micro_thumbnail_image")
        if isinstance(micro_thumb, QImage) and not micro_thumb.isNull():
            return QPixmap.fromImage(micro_thumb)

        return placeholder

    def _create_composite_thumbnail(self, rel: str, source: QPixmap, row: Dict[str, object]) -> QPixmap:
        """Generate and cache a resized/cropped thumbnail at the target display size."""

        # 1. Create a square target pixmap
        target_size = self._thumb_size
        composite = QPixmap(target_size)
        composite.fill(Qt.transparent)

        painter = QPainter(composite)
        try:
            # 2. Draw Source (Aspect Fill / Center Crop)
            view_w, view_h = target_size.width(), target_size.height()

            source_rect = calculate_center_crop(source.size(), target_size)

            if not source_rect.isEmpty():
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                painter.drawPixmap(QRectF(0.0, 0.0, float(view_w), float(view_h)), source, source_rect)

        finally:
            painter.end()

        self._composite_cache[rel] = composite
        return composite

    def _on_thumb_ready(self, root: Path, rel: str, pixmap: QPixmap) -> None:
        """Store thumbnails produced by :class:`ThumbnailLoader` and relay them."""

        if self._album_root and root != self._album_root:
            return
        self._thumb_cache[rel] = pixmap
        # Clear old composite if any
        self._composite_cache.pop(rel, None)
        # Note: We emit the raw pixmap here. The composite thumbnail for this `rel` will be generated
        # lazily the next time `resolve_thumbnail` is called for this `rel`, ensuring deferred composition.
        self.thumbnailReady.emit(root, rel, pixmap)

    def _placeholder_for(self, rel: str, is_video: bool) -> QPixmap:
        """Return a cached placeholder pixmap for *rel*."""

        cached = self._placeholder_cache.get(rel)
        if cached is not None:
            return cached

        suffix = Path(rel).suffix.lower().lstrip(".")
        if not suffix:
            suffix = "video" if is_video else "media"

        key = f"{suffix}|{is_video}"
        template = self._placeholder_templates.get(key)
        if template is None:
            template = self._create_placeholder(suffix)
            self._placeholder_templates[key] = template

        self._placeholder_cache[rel] = template
        return template

    def _create_placeholder(self, suffix: str) -> QPixmap:
        """Render a text-based placeholder pixmap for *suffix*."""

        canvas = QPixmap(self._thumb_size)
        canvas.fill(QColor("#1b1b1b"))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#f0f0f0"))

        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        painter.setFont(font)

        metrics = QFontMetrics(font)
        label = suffix.upper()
        text_width = metrics.horizontalAdvance(label)
        baseline = (canvas.height() + metrics.ascent()) // 2
        painter.drawText((canvas.width() - text_width) // 2, baseline, label)
        painter.end()

        return canvas

    @staticmethod
    def _normalise_path(path: Path) -> Path:
        """Return a consistently resolved form of *path* for comparisons."""

        try:
            return path.resolve()
        except OSError:
            return path
