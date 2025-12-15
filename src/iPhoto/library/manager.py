"""Basic library management: scanning, watching and editing albums."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal, QThreadPool, QMutex, QMutexLocker

from ..config import (
    ALBUM_MANIFEST_NAMES,
    EXPORT_DIR_NAME,
    RECENTLY_DELETED_DIR_NAME,
    WORK_DIR_NAME,
)
from ..errors import (
    AlbumDepthError,
    AlbumNameConflictError,
    AlbumOperationError,
    LibraryUnavailableError,
)
from ..media_classifier import classify_media
from ..models.album import Album
from ..utils.geocoding import resolve_location_name
from ..utils.jsonio import read_json
from ..cache.index_store import IndexStore
from ..utils.logging import get_logger
from .tree import AlbumNode

# Adjusted imports to point to the new location in library/workers
from .workers.scanner_worker import ScannerSignals, ScannerWorker
from .workers.rescan_worker import RescanSignals, RescanWorker

LOGGER = get_logger()

@dataclass(slots=True, frozen=True)
class GeotaggedAsset:
    """Lightweight descriptor describing an asset with GPS metadata."""

    library_relative: str
    """Relative path from the library root to the asset."""

    album_relative: str
    """Relative path from the asset's album root to the file."""

    absolute_path: Path
    """Absolute filesystem path to the asset."""

    album_path: Path
    """Root directory of the album that owns the asset."""

    asset_id: str
    """Identifier reported by the index row."""

    latitude: float
    longitude: float
    is_image: bool
    is_video: bool
    still_image_time: Optional[float]
    duration: Optional[float]
    location_name: Optional[str]
    """Human-readable label derived from the asset's GPS coordinate."""


class LibraryManager(QObject):
    """Manage the Basic Library tree, file-system helpers, and scanning state."""

    treeUpdated = Signal()
    errorRaised = Signal(str)

    # Scanner signals exposed for the facade
    scanProgress = Signal(Path, int, int)
    scanChunkReady = Signal(Path, list)
    scanFinished = Signal(Path, bool)
    scanBatchFailed = Signal(Path, int)

    _MAX_LIVE_BUFFER_SIZE = 5000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root: Path | None = None
        self._albums: list[AlbumNode] = []
        self._children: Dict[Path, list[AlbumNode]] = {}
        self._nodes: Dict[Path, AlbumNode] = {}
        self._deleted_dir: Path | None = None
        self._watcher = QFileSystemWatcher(self)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(500)
        # ``_watch_suspend_depth`` tracks how many in-flight operations asked us to
        # ignore file-system notifications. We use a counter instead of a boolean
        # to correctly handle nested operations that may overlap (e.g., multiple
        # concurrent file operations that each need to pause/resume the watcher).
        self._watch_suspend_depth = 0
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._debounce.timeout.connect(self._refresh_tree)

        # Scanner State
        self._current_scanner_worker: Optional[ScannerWorker] = None
        self._scan_thread_pool = QThreadPool.globalInstance()
        self._live_scan_buffer: List[Dict] = []
        self._live_scan_root: Optional[Path] = None
        self._scan_buffer_lock = QMutex()

    # ------------------------------------------------------------------
    # Basic properties
    # ------------------------------------------------------------------
    def root(self) -> Path | None:
        return self._root

    # ------------------------------------------------------------------
    # Binding and scanning
    # ------------------------------------------------------------------
    def bind_path(self, root: Path) -> None:
        # Clear existing watches to ensure initialization operations (like creating
        # the deleted items folder) do not trigger "directoryChanged" signals
        # from an active watcher, which would cause a double-refresh.
        if existing := self._watcher.directories():
            self._watcher.removePaths(existing)

        normalized = root.expanduser().resolve()
        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {root}")
        self._root = normalized
        self._initialize_deleted_dir()
        self._refresh_tree()

    def list_albums(self) -> list[AlbumNode]:
        return list(self._albums)

    def list_children(self, album: AlbumNode) -> list[AlbumNode]:
        return list(self._children.get(album.path, []))

    def scan_tree(self) -> list[AlbumNode]:
        self._refresh_tree()
        return self.list_albums()

    # ------------------------------------------------------------------
    # Scanning Logic
    # ------------------------------------------------------------------
    def start_scanning(self, root: Path, include: Iterable[str], exclude: Iterable[str]) -> None:
        """Start a background scan for the given root directory."""
        # Prepare signals outside the lock
        signals = ScannerSignals()
        signals.progressUpdated.connect(self.scanProgress)
        signals.chunkReady.connect(self._on_scan_chunk)
        signals.finished.connect(self._on_scan_finished)
        signals.error.connect(self._on_scan_error)
        signals.batchFailed.connect(self._on_scan_batch_failed)

        # Check if already scanning the same root (thread-safe)
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker is not None and self._live_scan_root:
            try:
                current_root = self._live_scan_root.resolve()
                requested_root = root.resolve()
            except OSError:
                current_root = self._live_scan_root
                requested_root = root

            # Keep scanning when the request targets the same tree (ancestor, descendant or sibling).
            if requested_root == current_root:
                return
            if requested_root in current_root.parents:
                return
            if current_root in requested_root.parents:
                return
            if self._paths_are_siblings(current_root, requested_root):
                return

            # Cancel the old scan before starting new one (inline to avoid deadlock)
            self._current_scanner_worker.cancel()
            self._current_scanner_worker = None
            self._live_scan_root = None

        self._live_scan_root = root
        self._live_scan_buffer.clear()

        worker = ScannerWorker(root, include, exclude, signals)
        self._current_scanner_worker = worker
        # Release lock before starting the worker
        del locker

        self._scan_thread_pool.start(worker)

    def stop_scanning(self) -> None:
        """Cancel the currently running scan, if any."""
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker:
            self._current_scanner_worker.cancel()
            self._current_scanner_worker = None
            # We don't clear the buffer immediately on stop, as the UI might still need it
            # until a new scan starts or the app closes. Setting root to None invalidates it contextually.
            self._live_scan_root = None

    def is_scanning_path(self, path: Path) -> bool:
        """Return True if the given path is covered by the active scan."""
        locker = QMutexLocker(self._scan_buffer_lock)
        if not self._live_scan_root:
            return False

        try:
            target = path.resolve()
            scan_root = self._live_scan_root.resolve()
            if target == scan_root:
                return True
            # Check if target is a subdirectory of scan_root
            return scan_root in target.parents
        except (OSError, ValueError):
            return False

    def get_live_scan_results(self, relative_to: Optional[Path] = None) -> List[Dict]:
        """Return a snapshot of valid items currently in the scan buffer.

        Args:
            relative_to: If provided, only returns items that are descendants of this path.
        """
        locker = QMutexLocker(self._scan_buffer_lock)
        if not self._live_scan_buffer:
            return []

        if relative_to is None:
            return list(self._live_scan_buffer)

        # Capture root inside lock to prevent race with stop_scanning
        scan_root = self._live_scan_root
        if not scan_root:
            return []

        # Optimization: Resolve paths once outside the loop to avoid I/O blocking per item.
        try:
            scan_root_res = scan_root.resolve()
            rel_root_res = relative_to.resolve()
        except OSError:
            return []

        # Determine the relationship between scan root and view root.
        # Case A: Same path. No path adjustment needed.
        if scan_root_res == rel_root_res:
            return list(self._live_scan_buffer)

        filtered = []
        # Case B: Scanning a child, viewing a parent (e.g., scan Vacation, view Photos).
        # We need to prepend the relative difference to the item paths.
        if rel_root_res in scan_root_res.parents:
            prefix = scan_root_res.relative_to(rel_root_res).as_posix()
            for item in self._live_scan_buffer:
                item_rel = item.get("rel")
                if not isinstance(item_rel, str) or not item_rel:
                    continue
                new_item = item.copy()
                new_item["rel"] = f"{prefix}/{item_rel}"
                filtered.append(new_item)

        # Case C: Scanning a parent, viewing a child (e.g., scan Photos, view Vacation).
        # We need to filter items that belong to the child and strip the prefix.
        elif scan_root_res in rel_root_res.parents:
            prefix = rel_root_res.relative_to(scan_root_res).as_posix()
            # We add a slash to ensure we match directory boundaries (e.g. "Vacation/" vs "VacationTrip")
            prefix_slash = f"{prefix}/"
            for item in self._live_scan_buffer:
                item_rel = item.get("rel")
                if not isinstance(item_rel, str):
                    continue
                # Check if the item is inside the viewing directory
                if item_rel == prefix or item_rel.startswith(prefix_slash):
                    new_item = item.copy()
                    # Strip the prefix to make it relative to the viewing directory
                    # e.g. "Vacation/img.jpg" -> "img.jpg"
                    new_item["rel"] = item_rel[len(prefix_slash):] if item_rel != prefix else ""
                    if not new_item["rel"]:
                        continue # Should not happen for files, but safeguard
                    filtered.append(new_item)

        # Case D: Disjoint paths (e.g. scan Photos/A, view Photos/B).
        # Return empty list.

        return filtered

    def _on_scan_chunk(self, root: Path, chunk: List[dict]) -> None:
        """Handle incoming scan chunks: update buffer only."""

        if not chunk:
            return

        # 1. Update In-Memory Buffer
        locker = QMutexLocker(self._scan_buffer_lock)
        # Check buffer limit
        if len(self._live_scan_buffer) < self._MAX_LIVE_BUFFER_SIZE:
            self._live_scan_buffer.extend(chunk)
        else:
            # If buffer is full, we rely on disk.
            # We can optionally rotate, but simply stopping accumulation is safer for memory.
            # The consuming models should have already pulled earlier data.
            LOGGER.warning(
                f"Live scan buffer for {root} reached its limit of {self._MAX_LIVE_BUFFER_SIZE} items. "
                f"{len(chunk)} new items were not added to the in-memory buffer; relying on disk persistence."
            )

        # 2. Forward signal
        # The persistence is now handled by the ScannerWorker in the background thread.
        self.scanChunkReady.emit(root, chunk)

    def _on_scan_finished(self, root: Path, rows: List[dict]) -> None:
        # Emit scanFinished for downstream handling (e.g., updating links or finalizing scan).
        self.scanFinished.emit(root, True)
        # Clear worker reference after emitting signal to prevent race conditions
        locker = QMutexLocker(self._scan_buffer_lock)
        self._current_scanner_worker = None

    def _on_scan_error(self, root: Path, message: str) -> None:
        locker = QMutexLocker(self._scan_buffer_lock)
        self._current_scanner_worker = None
        self.errorRaised.emit(message)
        self.scanFinished.emit(root, False)

    def _on_scan_batch_failed(self, root: Path, count: int) -> None:
        """Propagate partial failure notifications to the UI."""
        self.scanBatchFailed.emit(root, count)

    def _paths_equal(self, p1: Path, p2: Path) -> bool:
        try:
            return p1.resolve() == p2.resolve()
        except OSError:
            return p1 == p2

    def _paths_are_siblings(self, p1: Path, p2: Path) -> bool:
        """Return True when *p1* and *p2* share the same parent directory."""
        parent1 = p1.parent
        parent2 = p2.parent
        if parent1 == p1 or parent2 == p2:
            return False
        if not (parent1 and parent2):
            return False
        if p1 in parent2.parents or p2 in parent1.parents:
            return False
        return parent1 == parent2

    # ------------------------------------------------------------------
    # Asset helpers
    # ------------------------------------------------------------------
    def get_geotagged_assets(self) -> List[GeotaggedAsset]:
        """Return every asset in the library that exposes GPS coordinates."""

        root = self._require_root()
        # ``seen`` prevents duplicate entries when a sub-album and its parent
        # both reference the same physical file in their indexes.
        seen: set[Path] = set()
        assets: list[GeotaggedAsset] = []

        album_paths: set[Path] = {root}
        album_paths.update(self._nodes.keys())

        for album_path in sorted(album_paths):
            try:
                # Optimized query: only fetch rows that actually have GPS data.
                rows = IndexStore(album_path).read_geotagged()
            except Exception:
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                gps = row.get("gps")
                if not isinstance(gps, dict):
                    continue
                lat = gps.get("lat")
                lon = gps.get("lon")
                if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                    continue
                # ``resolve_location_name`` maps the GPS coordinate to a human-readable
                # label (typically the city) so that low zoom levels can show a
                # meaningful aggregate marker instead of individual thumbnails.
                location_name = resolve_location_name(gps)
                rel = row.get("rel")
                if not isinstance(rel, str) or not rel:
                    continue
                abs_path = (album_path / rel).resolve()
                if abs_path in seen:
                    continue
                seen.add(abs_path)
                try:
                    library_relative_path = abs_path.relative_to(root)
                    library_relative_str = library_relative_path.as_posix()
                except ValueError:
                    library_relative_str = abs_path.name
                asset_id = str(row.get("id") or rel)
                classified_image, classified_video = classify_media(row)
                # Combine classifier results with any persisted flags to remain
                # compatible with older index rows that stored boolean values.
                is_image = classified_image or bool(row.get("is_image"))
                is_video = classified_video or bool(row.get("is_video"))
                still_image_time = row.get("still_image_time")
                if isinstance(still_image_time, (int, float)):
                    still_image_value: Optional[float] = float(still_image_time)
                else:
                    still_image_value = None
                duration = row.get("dur")
                if isinstance(duration, (int, float)):
                    duration_value: Optional[float] = float(duration)
                else:
                    duration_value = None
                assets.append(
                    GeotaggedAsset(
                        library_relative=library_relative_str,
                        album_relative=rel,
                        absolute_path=abs_path,
                        album_path=album_path,
                        asset_id=asset_id,
                        latitude=float(lat),
                        longitude=float(lon),
                        is_image=is_image,
                        is_video=is_video,
                        still_image_time=still_image_value,
                        duration=duration_value,
                        location_name=location_name,
                    )
                )

        assets.sort(key=lambda item: item.library_relative)
        return assets

    # ------------------------------------------------------------------
    # Album creation helpers
    # ------------------------------------------------------------------
    def create_album(self, name: str) -> AlbumNode:
        root = self._require_root()
        target = self._validate_new_name(root, name)
        target.mkdir(parents=False, exist_ok=False)
        node = AlbumNode(target, 1, target.name, False)
        self.ensure_manifest(node)
        self._refresh_tree()
        return self._node_for_path(target)

    def ensure_deleted_directory(self) -> Path:
        """Create the dedicated trash directory when missing and return it."""

        root = self._require_root()
        target = root / RECENTLY_DELETED_DIR_NAME
        self._migrate_legacy_deleted_dir(root, target)
        if target.exists() and not target.is_dir():
            raise AlbumOperationError(
                f"Deleted items path exists but is not a directory: {target}"
            )
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AlbumOperationError(
                f"Could not prepare deleted items folder: {exc}"
            ) from exc
        self._deleted_dir = target
        return target

    def deleted_directory(self) -> Path | None:
        """Return the path to the trash directory, creating it on demand."""

        if self._root is None:
            self._deleted_dir = None
            return None
        cached = self._deleted_dir
        if cached is not None and cached.exists():
            return cached
        try:
            return self.ensure_deleted_directory()
        except AlbumOperationError as exc:
            self.errorRaised.emit(str(exc))
            return None

    def create_subalbum(self, parent: AlbumNode, name: str) -> AlbumNode:
        if parent.level != 1:
            raise AlbumDepthError("Sub-albums can only be created under top-level albums.")
        root = self._require_root()
        if not parent.path.is_relative_to(root):
            parent_path = parent.path.resolve()
            if not str(parent_path).startswith(str(root)):
                raise AlbumOperationError("Parent album is outside the library root.")
        target = self._validate_new_name(parent.path, name)
        target.mkdir(parents=False, exist_ok=False)
        node = AlbumNode(target, 2, target.name, False)
        self.ensure_manifest(node)
        self._refresh_tree()
        return self._node_for_path(target)

    def rename_album(self, node: AlbumNode, new_name: str) -> None:
        parent = node.path.parent
        target = self._validate_new_name(parent, new_name)
        try:
            node.path.rename(target)
        except FileExistsError as exc:
            raise AlbumNameConflictError(f"An album named '{new_name}' already exists.") from exc
        except OSError as exc:  # pragma: no cover - defensive guard
            raise AlbumOperationError(str(exc)) from exc
        # ``Album.open`` now normalises and persists manifest updates so the
        # metadata stays aligned with the renamed directory immediately.
        Album.open(target)
        self._refresh_tree()

    def ensure_manifest(self, node: AlbumNode) -> Path:
        Album.open(node.path)
        marker = node.path / ".iphoto.album"
        if not marker.exists():
            marker.touch()
        manifest = self._find_manifest(node.path)
        if manifest is None:
            manifest = node.path / ALBUM_MANIFEST_NAMES[0]
        return manifest

    def find_album_by_uuid(self, album_id: str) -> Optional[AlbumNode]:
        """Return the library node whose manifest declares *album_id*.

        The lookup tolerates missing or unreadable manifests and merely skips
        those entries so the remaining albums keep their fast-path resolution.
        ``album_id`` comparisons are performed case-insensitively to avoid
        surprises when legacy manifests contain uppercase UUIDs.
        """

        if not album_id:
            return None
        normalized = album_id.strip()
        if not normalized:
            return None
        needle = normalized.casefold()

        # The library root is not included in ``self._nodes`` because the tree
        # structure focuses on first- and second-level albums.  However,
        # trashed assets can originate directly from the root (for example when
        # deleted via the "All Photos" aggregate view).  Those entries store
        # the root's UUID, so we need to compare against the root manifest
        # explicitly before scanning child nodes.
        root = self._root
        if root is not None:
            manifest_path = self._find_manifest(root)
            if manifest_path is not None:
                try:
                    data = read_json(manifest_path)
                except Exception as exc:  # pragma: no cover - defensive guard
                    # Surfacing the failure keeps the UI informed without
                    # breaking the fallback search that follows.
                    self.errorRaised.emit(f"Failed to read root manifest: {exc}")
                else:
                    candidate = data.get("id")
                    if isinstance(candidate, str) and candidate.strip().casefold() == needle:
                        try:
                            album = Album.open(root)
                        except Exception as exc:  # pragma: no cover - defensive guard
                            # If opening the album fails we cannot build a
                            # representative node, so emit the error and allow
                            # the regular search to continue.
                            self.errorRaised.emit(f"Failed to open root album: {exc}")
                        else:
                            title = album.manifest.get("title")
                            if not isinstance(title, str) or not title:
                                title = root.name
                            return AlbumNode(root, level=0, title=title, has_manifest=True)
        for path, node in self._nodes.items():
            manifest_path = self._find_manifest(path)
            if manifest_path is None:
                continue
            try:
                data = read_json(manifest_path)
            except Exception as exc:  # pragma: no cover - defensive guard
                self.errorRaised.emit(str(exc))
                continue
            candidate = data.get("id")
            if isinstance(candidate, str) and candidate.strip().casefold() == needle:
                return node
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _require_root(self) -> Path:
        if self._root is None:
            raise LibraryUnavailableError("Basic Library path has not been configured.")
        return self._root

    def _refresh_tree(self) -> None:
        if self._root is None:
            self._albums = []
            self._children = {}
            self._nodes = {}
            self._deleted_dir = None
            self._rebuild_watches()
            self.treeUpdated.emit()
            return
        albums: list[AlbumNode] = []
        children: Dict[Path, list[AlbumNode]] = {}
        nodes: Dict[Path, AlbumNode] = {}
        for album_dir in self._iter_album_dirs(self._root):
            node = self._build_node(album_dir, level=1)
            albums.append(node)
            nodes[album_dir] = node
            child_nodes = [self._build_node(child, level=2) for child in self._iter_album_dirs(album_dir)]
            for child in child_nodes:
                nodes[child.path] = child
            children[album_dir] = child_nodes
        self._albums = sorted(albums, key=lambda item: item.title.casefold())
        self._children = {parent: sorted(kids, key=lambda item: item.title.casefold()) for parent, kids in children.items()}
        self._nodes = nodes
        self._rebuild_watches()
        self.treeUpdated.emit()

    def _initialize_deleted_dir(self) -> None:
        """Prepare the deleted-items directory while swallowing recoverable errors."""

        if self._root is None:
            self._deleted_dir = None
            return
        try:
            self.ensure_deleted_directory()
        except AlbumOperationError as exc:
            # Creation failures are surfaced to the UI while the library remains usable.
            self._deleted_dir = None
            self.errorRaised.emit(str(exc))

    def _iter_album_dirs(self, root: Path) -> Iterable[Path]:
        try:
            entries = list(root.iterdir())
        except OSError as exc:  # pragma: no cover - filesystem failure
            self.errorRaised.emit(str(exc))
            return []
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name == WORK_DIR_NAME:
                continue
            if entry.name == RECENTLY_DELETED_DIR_NAME:
                # The trash folder should stay hidden from the regular album list
                # so that it only appears through the dedicated "Recently Deleted"
                # entry in the sidebar.
                continue
            if entry.name == EXPORT_DIR_NAME:
                continue
            yield entry

    def _migrate_legacy_deleted_dir(self, root: Path, target: Path) -> None:
        """Move data from the legacy ``.iPhoto/deleted`` path into *target*.

        Earlier builds stored trashed assets inside ``.iPhoto/deleted`` which
        made the collection difficult to locate from outside the application.
        When upgrading we want to preserve any existing deletions by moving the
        entire folder into the new root-level trash.  When a plain rename is not
        possible we fall back to copying individual entries while avoiding
        filename collisions.
        """

        legacy = root / WORK_DIR_NAME / "deleted"
        if not legacy.exists() or not legacy.is_dir():
            return

        try:
            if not target.exists():
                legacy.rename(target)
                return
        except OSError as exc:
            raise AlbumOperationError(
                f"Could not migrate legacy deleted folder: {exc}"
            ) from exc

        for entry in legacy.iterdir():
            if entry.name == WORK_DIR_NAME:
                destination_parent = target / WORK_DIR_NAME
                destination_parent.mkdir(parents=True, exist_ok=True)
                for child in entry.iterdir():
                    destination = self._unique_child_path(
                        destination_parent, child.name
                    )
                    try:
                        shutil.move(str(child), str(destination))
                    except OSError as exc:
                        raise AlbumOperationError(
                            f"Could not migrate legacy deleted cache '{child}': {exc}"
                        ) from exc
                continue

            destination = self._unique_child_path(target, entry.name)
            try:
                shutil.move(str(entry), str(destination))
            except OSError as exc:
                raise AlbumOperationError(
                    f"Could not migrate legacy deleted entry '{entry}': {exc}"
                ) from exc

        try:
            legacy.rmdir()
        except OSError:
            # Leaving the empty folder behind is harmless and avoids masking
            # migration successes when the directory still contains temporary
            # files created by external tools.
            pass

    def _unique_child_path(self, parent: Path, name: str) -> Path:
        """Return a path under *parent* that avoids overwriting existing files."""

        candidate = parent / name
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while True:
            next_candidate = parent / f"{stem} ({counter}){suffix}"
            if not next_candidate.exists():
                return next_candidate
            counter += 1

    def _build_node(self, path: Path, *, level: int) -> AlbumNode:
        title, has_manifest = self._describe_album(path)
        return AlbumNode(path, level, title, has_manifest)

    def _describe_album(self, path: Path) -> tuple[str, bool]:
        manifest = self._find_manifest(path)
        if manifest:
            try:
                data = read_json(manifest)
            except Exception as exc:  # pragma: no cover - invalid JSON
                self.errorRaised.emit(str(exc))
            else:
                title = str(data.get("title") or path.name)
                return title, True
            return path.name, True
        marker = path / ".iphoto.album"
        if marker.exists():
            return path.name, True
        return path.name, False

    def _find_manifest(self, path: Path) -> Path | None:
        for name in ALBUM_MANIFEST_NAMES:
            candidate = path / name
            if candidate.exists():
                return candidate
        return None

    def _validate_new_name(self, parent: Path, name: str) -> Path:
        candidate = name.strip()
        if not candidate:
            raise AlbumOperationError("Album name cannot be empty.")
        if Path(candidate).name != candidate:
            raise AlbumOperationError("Album name must not contain path separators.")
        target = parent / candidate
        if target.exists():
            raise AlbumNameConflictError(f"An album named '{candidate}' already exists.")
        return target

    def pause_watcher(self) -> None:
        """Temporarily suppress change notifications during internal writes."""

        # Increment the suspension depth so nested pause calls continue to be
        # reference-counted.  The debounce timer is stopped on the first pause
        # to ensure that an earlier notification does not race with the write we
        # are about to perform.
        self._watch_suspend_depth += 1
        if self._watch_suspend_depth == 1 and self._debounce.isActive():
            self._debounce.stop()

    def resume_watcher(self) -> None:
        """Re-enable change notifications once protected writes have finished."""

        if self._watch_suspend_depth == 0:
            return
        self._watch_suspend_depth -= 1

    def _on_directory_changed(self, path: str) -> None:
        # Skip notifications while we are in the middle of an internally
        # triggered write such as a manifest save.  The associated UI components
        # already know about those updates, so reacting to the file-system event
        # would only cause redundant reloads.
        if self._watch_suspend_depth > 0:
            return

        # ``QFileSystemWatcher`` emits plain strings.  Queue a debounced refresh
        # whenever a change notification arrives so the sidebar reflects
        # external edits without thrashing the filesystem.
        self._debounce.start()

    def _rebuild_watches(self) -> None:
        current = set(self._watcher.directories())
        desired: set[str] = set()
        if self._root is not None:
            desired.add(str(self._root))
            desired.update(str(node.path) for node in self._albums)
        remove = [path for path in current if path not in desired]
        if remove:
            self._watcher.removePaths(remove)
        add = [path for path in desired if path not in current]
        if add:
            self._watcher.addPaths(add)

    def _node_for_path(self, path: Path) -> AlbumNode:
        node = self._nodes.get(path)
        if node is not None:
            return node
        resolved = path.resolve()
        node = self._nodes.get(resolved)
        if node is not None:
            return node
        raise AlbumOperationError(f"Album node not found for path: {path}")

__all__ = ["LibraryManager"]
