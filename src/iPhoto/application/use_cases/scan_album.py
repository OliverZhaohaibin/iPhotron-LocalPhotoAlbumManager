import logging
import os
import queue
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import List, Set, Dict, Optional, Any
from dataclasses import dataclass

from iPhoto.application.dtos import ScanAlbumRequest, ScanAlbumResponse
from iPhoto.domain.models import Album, Asset, MediaType
from iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.events.bus import EventBus, Event
from iPhoto.application.interfaces import IMetadataProvider, IThumbnailGenerator
from iPhoto.config import WORK_DIR_NAME, EXPORT_DIR_NAME, DEFAULT_INCLUDE, DEFAULT_EXCLUDE
from iPhoto.utils.pathutils import should_include

@dataclass(kw_only=True)
class AlbumScannedEvent(Event):
    album_id: str
    added_count: int
    updated_count: int
    deleted_count: int

class FileDiscoveryThread(threading.Thread):
    def __init__(self, root: Path, queue_obj: queue.Queue, include: List[str] = None, exclude: List[str] = None):
        super().__init__(name=f"ScannerDiscovery-{root.name}")
        self._root = root
        self._queue = queue_obj
        self._include = include or DEFAULT_INCLUDE
        self._exclude = exclude or DEFAULT_EXCLUDE
        self._stop_event = threading.Event()
        self.total_found = 0
        self.daemon = True

    def run(self):
        try:
            for dirpath, dirnames, filenames in os.walk(self._root):
                if self._stop_event.is_set():
                    break

                # Prune internal dirs
                dirnames[:] = [d for d in dirnames if d != WORK_DIR_NAME and d != EXPORT_DIR_NAME]

                for name in filenames:
                    if self._stop_event.is_set():
                        break

                    candidate = Path(dirpath) / name

                    if should_include(candidate, self._include, self._exclude, root=self._root):
                        self._queue.put(candidate)
                        self.total_found += 1
        finally:
            self._queue.put(None) # Signal end

    def stop(self):
        self._stop_event.set()

class ScanAlbumUseCase:
    def __init__(
        self,
        album_repo: IAlbumRepository,
        asset_repo: IAssetRepository,
        event_bus: EventBus,
        metadata_provider: IMetadataProvider,
        thumbnail_generator: IThumbnailGenerator
    ):
        self._album_repo = album_repo
        self._asset_repo = asset_repo
        self._events = event_bus
        self._metadata = metadata_provider
        self._thumbnails = thumbnail_generator
        self._logger = logging.getLogger(__name__)

    def execute(self, request: ScanAlbumRequest) -> ScanAlbumResponse:
        self._logger.info(f"Scanning album {request.album_id}")

        album = self._album_repo.get(request.album_id)
        if not album:
            raise ValueError(f"Album {request.album_id} not found")

        # 1. Load existing assets
        existing_assets = self._asset_repo.get_by_album(album.id)
        existing_map: Dict[str, Asset] = {str(a.path): a for a in existing_assets}

        # 2. Start Discovery
        # We could potentially load include/exclude rules from Album manifest if available
        # For now, using defaults via FileDiscoveryThread default args
        path_queue = queue.Queue(maxsize=1000)
        discoverer = FileDiscoveryThread(album.path, path_queue)
        discoverer.start()

        found_paths: Set[str] = set()
        processed_ids: Set[str] = set() # Track IDs of assets found/updated
        added_count = 0
        updated_count = 0
        batch: List[Path] = []
        BATCH_SIZE = 50

        def process_batch(paths: List[Path]):
            nonlocal added_count, updated_count

            # Fetch metadata for batch
            meta_batch = self._metadata.get_metadata_batch(paths)

            # Map source file to metadata (handling resolving/normalization)
            meta_lookup = {}
            for m in meta_batch:
                src = m.get("SourceFile")
                if src:
                    meta_lookup[src] = m
                    meta_lookup[unicodedata.normalize('NFC', src)] = m
                    meta_lookup[unicodedata.normalize('NFD', src)] = m

            assets_to_save = []

            for path in paths:
                rel_path = path.relative_to(album.path)
                str_rel_path = str(rel_path)
                found_paths.add(str_rel_path)

                # Check cache (incremental scan)
                existing = existing_map.get(str_rel_path)
                stat = path.stat()

                # If existing and modified time/size matches, skip full process
                if existing:
                    # Tolerance 1 sec
                    existing_ts = int(existing.created_at.timestamp() * 1_000_000) if existing.created_at else 0
                    current_ts = int(stat.st_mtime * 1_000_000)

                    if existing.size_bytes == stat.st_size and abs(existing_ts - current_ts) <= 1_000_000:
                        # Cache hit
                        # Check for missing micro-thumbnail
                        # (Not implementing backfill here for simplicity, but could be added)
                        processed_ids.add(existing.id)
                        continue

                # Process new/changed
                raw_meta = meta_lookup.get(path.as_posix())
                if not raw_meta:
                    raw_meta = meta_lookup.get(unicodedata.normalize('NFC', path.as_posix()))

                # Normalize
                row = self._metadata.normalize_metadata(album.path, path, raw_meta or {})

                # Generate micro-thumb
                if row.get("media_type") == 0: # Image
                     mt = self._thumbnails.generate_micro_thumbnail(path)
                     if mt:
                         row["micro_thumbnail"] = mt

                # Convert to Domain Entity
                # row['id'] comes from provider as "as_{hash}".
                # We prioritize existing.id to preserve relationships if the file content (hash) hasn't changed.
                # However, if content changed (hash changed), row['id'] will differ.
                # If we are in this block, it means either it's new, OR it's existing but modified (cache miss).
                # If modified, we should arguably keep the stable ID if possible (by path),
                # BUT the system relies on hash-based IDs for deduplication.
                # The legacy system logic: ID is hash based.
                # If we use existing.id, we might mask content changes if ID is strictly hash.
                # BUT, if we change ID, we lose favorites/album inclusion that references the ID.
                # Standard practice: Keep ID stable if path is same, update hash/content.

                asset_id = existing.id if existing else row['id']

                # Map media type int to enum
                mt_enum = MediaType.IMAGE if row.get("media_type") == 0 else MediaType.VIDEO

                # Metadata dict
                meta_json = {k: v for k, v in row.items() if k not in ['id', 'rel', 'bytes', 'dt', 'ts', 'media_type', 'is_favorite', 'parent_album_path', 'micro_thumbnail']}
                if "micro_thumbnail" in row:
                    meta_json["micro_thumbnail"] = row["micro_thumbnail"]

                asset = Asset(
                    id=asset_id,
                    album_id=album.id,
                    path=rel_path,
                    media_type=mt_enum,
                    size_bytes=row['bytes'],
                    created_at=datetime.fromtimestamp(row['ts'] / 1_000_000) if row.get('ts') else None,
                    parent_album_path=None, # Will be set by repo or service context?
                    is_favorite=existing.is_favorite if existing else False,
                    width=row.get('w'),
                    height=row.get('h'),
                    duration=row.get('duration'),
                    metadata=meta_json,
                    content_identifier=row.get('content_identifier'),
                    live_photo_group_id=existing.live_photo_group_id if existing else None
                )

                assets_to_save.append(asset)
                processed_ids.add(asset.id)
                if existing:
                    updated_count += 1
                else:
                    added_count += 1

            if assets_to_save:
                self._asset_repo.save_batch(assets_to_save)


        # Consume queue
        while True:
            path = path_queue.get()
            if path is None:
                break

            batch.append(path)
            if len(batch) >= BATCH_SIZE:
                process_batch(batch)
                batch = []

        # Flush remaining
        if batch:
            process_batch(batch)

        # 3. Identify Deletions
        # Deletion logic:
        # An asset is deleted if its path was not found AND its ID was not encountered/processed elsewhere.
        # This protects against deleting an asset that was just moved (same ID, new path).
        deleted_ids = []
        for path_str, asset in existing_map.items():
            if path_str not in found_paths:
                # If ID was seen in processed_ids (meaning it was found at another path), do not delete.
                if asset.id not in processed_ids:
                    deleted_ids.append(asset.id)

        for did in deleted_ids:
            self._asset_repo.delete(did)

        deleted_count = len(deleted_ids)

        self._events.publish(AlbumScannedEvent(
            album_id=album.id,
            added_count=added_count,
            updated_count=updated_count,
            deleted_count=deleted_count
        ))

        return ScanAlbumResponse(
            added_count=added_count,
            updated_count=updated_count,
            deleted_count=deleted_count
        )
