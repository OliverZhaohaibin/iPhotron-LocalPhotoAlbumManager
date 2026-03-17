import logging
import mimetypes
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from iPhoto import _native
from iPhoto.application.interfaces import IMetadataProvider
from iPhoto.io.metadata import read_image_meta_with_exiftool, read_video_meta
from iPhoto.utils.exiftool import get_metadata_batch
from iPhoto.utils.hashutils import compute_file_id

logger = logging.getLogger(__name__)


def _parse_iso8601_metadata(dt_value: object) -> tuple[int, int | None, int | None] | None:
    if not isinstance(dt_value, str) or not dt_value:
        return None

    native_result = _native.parse_iso8601_full(dt_value)
    if native_result is not None:
        ts, year, month = native_result
        return ts, year, month

    try:
        dt_obj = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return int(dt_obj.timestamp() * 1_000_000), dt_obj.year, dt_obj.month


def _default_dt_from_mtime(stat_mtime: float) -> str:
    return datetime.fromtimestamp(stat_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _media_hint_from_suffix(suffix: str) -> int:
    lowered = suffix.lower()
    if lowered in ExifToolMetadataProvider._IMAGE_EXTENSIONS:
        return _native.MEDIA_HINT_IMAGE
    if lowered in ExifToolMetadataProvider._VIDEO_EXTENSIONS:
        return _native.MEDIA_HINT_VIDEO
    return _native.MEDIA_HINT_UNKNOWN


def _resolve_media_type(suffix: str, mime: object, preferred: int | None = None) -> int | None:
    if preferred == _native.MEDIA_HINT_IMAGE:
        return 0
    if preferred == _native.MEDIA_HINT_VIDEO:
        return 1

    lowered = suffix.lower()
    if lowered in ExifToolMetadataProvider._VIDEO_EXTENSIONS:
        return 1
    if lowered in ExifToolMetadataProvider._IMAGE_EXTENSIONS:
        return 0
    if isinstance(mime, str) and mime.startswith("video/"):
        return 1
    if isinstance(mime, str) and mime.startswith("image/"):
        return 0
    return None


class ExifToolMetadataProvider(IMetadataProvider):
    _IMAGE_EXTENSIONS = {".heic", ".heif", ".heifs", ".heicf", ".jpg", ".jpeg", ".png", ".webp"}
    _VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".qt", ".avi", ".mkv"}

    def get_metadata_batch(self, paths: List[Path]) -> List[Dict[str, Any]]:
        try:
            return get_metadata_batch(paths)
        except Exception as exc:
            logger.error("Failed to get metadata batch: %s", exc)
            return []

    def normalize_metadata(self, root: Path, file_path: Path, raw_metadata: Dict[str, Any]) -> Dict[str, Any]:
        stat = file_path.stat()
        processed_meta = self._extract_processed_metadata(file_path, raw_metadata)
        default_dt = _default_dt_from_mtime(stat.st_mtime)
        dt_candidate = processed_meta.get("dt")
        dt_value = dt_candidate if isinstance(dt_candidate, str) else default_dt
        parsed_dt = _parse_iso8601_metadata(dt_value)

        if parsed_dt is not None:
            ts, year, month = parsed_dt
        else:
            ts = int(stat.st_mtime * 1_000_000)
            year = None
            month = None

        return self._build_row(
            root,
            file_path,
            stat.st_size,
            default_dt,
            processed_meta,
            file_id=compute_file_id(file_path),
            ts=ts,
            year=year,
            month=month,
            media_type=_resolve_media_type(file_path.suffix, processed_meta.get("mime")),
        )

    def prepare_scan_chunk(
        self,
        root: Path,
        paths: List[Path],
        meta_lookup: Dict[str, Dict[str, Any]],
    ) -> list[tuple[Path, Dict[str, Any]]] | None:
        if not paths:
            return []

        native_inputs: list[_native.NativePrepareScanInput] = []
        prepared_entries: list[tuple[Path, Dict[str, Any], Dict[str, Any], str, int]] = []

        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue

            raw_metadata = self._resolve_raw_metadata(path, meta_lookup)
            processed_meta = self._extract_processed_metadata(path, raw_metadata)
            default_dt = _default_dt_from_mtime(stat.st_mtime)
            dt_candidate = processed_meta.get("dt")
            dt_value = dt_candidate if isinstance(dt_candidate, str) else default_dt

            native_inputs.append(
                _native.NativePrepareScanInput(
                    abs_path=str(path),
                    rel_path=path.relative_to(root).as_posix(),
                    size_bytes=stat.st_size,
                    mtime_us=int(stat.st_mtime * 1_000_000),
                    dt_value=dt_value,
                    media_hint=_media_hint_from_suffix(path.suffix),
                )
            )
            prepared_entries.append((path, raw_metadata, processed_meta, default_dt, stat.st_size))

        native_results = _native.prepare_scan_chunk(native_inputs)
        if native_results is None or len(native_results) != len(prepared_entries):
            return None

        rows: list[tuple[Path, Dict[str, Any]]] = []
        for entry, result in zip(prepared_entries, native_results, strict=False):
            path, raw_metadata, processed_meta, default_dt, size_bytes = entry

            if not result.ok or result.file_id is None or result.ts is None:
                try:
                    rows.append((path, self.normalize_metadata(root, path, raw_metadata)))
                except Exception:
                    continue
                continue

            rows.append(
                (
                    path,
                    self._build_row(
                        root,
                        path,
                        size_bytes,
                        default_dt,
                        processed_meta,
                        file_id=result.file_id,
                        ts=result.ts,
                        year=result.year if result.year and result.year > 0 else None,
                        month=result.month if result.month and result.month > 0 else None,
                        media_type=_resolve_media_type(
                            path.suffix,
                            processed_meta.get("mime"),
                            preferred=result.media_type,
                        ),
                    ),
                )
            )

        return rows

    def _extract_processed_metadata(
        self,
        file_path: Path,
        raw_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        suffix = file_path.suffix.lower()
        if suffix in self._IMAGE_EXTENSIONS:
            return read_image_meta_with_exiftool(file_path, raw_metadata)
        if suffix in self._VIDEO_EXTENSIONS:
            return read_video_meta(file_path, raw_metadata)
        return {}

    def _build_row(
        self,
        root: Path,
        file_path: Path,
        size_bytes: int,
        default_dt: str,
        processed_meta: Dict[str, Any],
        *,
        file_id: str,
        ts: int,
        year: int | None,
        month: int | None,
        media_type: int | None,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "rel": file_path.relative_to(root).as_posix(),
            "bytes": size_bytes,
            "dt": default_dt,
            "ts": ts,
            "id": f"as_{file_id}",
            "mime": mimetypes.guess_type(file_path.name)[0],
        }

        for key, value in processed_meta.items():
            if value is not None:
                row[key] = value

        row["ts"] = ts
        row["year"] = year
        row["month"] = month

        width = row.get("w")
        height = row.get("h")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and height > 0:
            row["aspect_ratio"] = float(width) / float(height)
        else:
            row["aspect_ratio"] = None

        row["media_type"] = media_type
        return row

    def _resolve_raw_metadata(
        self,
        path: Path,
        meta_lookup: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        raw_meta = meta_lookup.get(path.as_posix())
        if not raw_meta:
            raw_meta = meta_lookup.get(unicodedata.normalize("NFC", path.as_posix()))
        return raw_meta or {}
