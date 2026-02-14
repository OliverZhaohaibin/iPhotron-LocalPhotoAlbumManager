"""Pure functions that convert domain Assets / scan-rows to AssetDTOs."""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from iPhoto.application.dtos import AssetDTO
from iPhoto.config import WORK_DIR_NAME
from iPhoto.domain.models import Asset
from iPhoto.domain.models.core import MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.utils import image_loader

# ── thumbnail-detection constants ────────────────────────────────────────────
THUMBNAIL_SUFFIX_RE = re.compile(r"_(\d{2,4})x(\d{2,4})(?=\.[^.]+$)", re.IGNORECASE)
THUMBNAIL_MAX_DIMENSION = 512
THUMBNAIL_MAX_BYTES = 350_000
LEGACY_THUMB_DIRS = {WORK_DIR_NAME.lower(), ".photo", ".iphoto"}


# ── path helpers ─────────────────────────────────────────────────────────────

def resolve_abs_path(rel_path: Path, library_root: Optional[Path]) -> Path:
    if rel_path.is_absolute():
        return rel_path
    if library_root:
        try:
            return (library_root / rel_path).resolve()
        except OSError:
            return library_root / rel_path
    return rel_path.resolve()


# ── thumbnail helpers ────────────────────────────────────────────────────────

def is_legacy_thumb_path(rel_path: Path) -> bool:
    for part in rel_path.parts:
        if part.lower() in LEGACY_THUMB_DIRS:
            return True
    return False


def is_thumbnail_asset(asset: Asset) -> bool:
    rel_path = asset.path
    if is_legacy_thumb_path(rel_path):
        return True

    match = THUMBNAIL_SUFFIX_RE.search(rel_path.name)
    if not match:
        return False
    try:
        width = int(match.group(1))
        height = int(match.group(2))
    except ValueError:
        return False
    if max(width, height) > THUMBNAIL_MAX_DIMENSION:
        return False

    meta = asset.metadata or {}
    row_w = asset.width or meta.get("w") or meta.get("width")
    row_h = asset.height or meta.get("h") or meta.get("height")
    try:
        if row_w is not None and row_h is not None:
            if int(row_w) != width or int(row_h) != height:
                return False
    except (TypeError, ValueError):
        return False

    size_bytes = asset.size_bytes or meta.get("bytes")
    try:
        if size_bytes is not None and int(size_bytes) > THUMBNAIL_MAX_BYTES:
            return False
    except (TypeError, ValueError):
        return False

    return True


def scan_row_is_thumbnail(rel: str, row: dict) -> bool:
    rel_path = Path(rel)
    if is_legacy_thumb_path(rel_path):
        return True
    match = THUMBNAIL_SUFFIX_RE.search(rel_path.name)
    if not match:
        return False
    try:
        width = int(match.group(1))
        height = int(match.group(2))
    except ValueError:
        return False
    if max(width, height) > THUMBNAIL_MAX_DIMENSION:
        return False
    row_w = row.get("w") or row.get("width")
    row_h = row.get("h") or row.get("height")
    try:
        if row_w is not None and row_h is not None:
            if int(row_w) != width or int(row_h) != height:
                return False
    except (TypeError, ValueError):
        return False
    size_bytes = row.get("bytes")
    try:
        if size_bytes is not None and int(size_bytes) > THUMBNAIL_MAX_BYTES:
            return False
    except (TypeError, ValueError):
        return False
    return True


# ── Asset → DTO conversion ───────────────────────────────────────────────────

def _coerce_positive_number(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def to_dto(asset: Asset, library_root: Optional[Path]) -> AssetDTO:
    """Convert a domain *Asset* to an *AssetDTO*."""
    abs_path = resolve_abs_path(asset.path, library_root)

    # Robust conversion: handle both str-Enum and IntEnum/integer cases
    mt_raw = asset.media_type
    if hasattr(mt_raw, "value"):
        mt = str(mt_raw.value)
    else:
        mt = str(mt_raw)

    # Map integer/legacy values to DTO expectations
    if mt in ("1", "2", "MediaType.VIDEO"):
        mt = "video"
    elif mt in ("0", "MediaType.IMAGE"):
        mt = "image"

    is_video = (mt == "video")
    is_image_type = mt in {"image", "photo"}
    # Live photo check
    is_live = (mt == "live") or (asset.live_photo_group_id is not None)
    if is_video and asset.live_photo_group_id is not None:
        is_live = False
    if not is_live and asset.metadata:
        live_partner = asset.metadata.get("live_partner_rel")
        live_role = asset.metadata.get("live_role")
        if live_partner and live_role != 1 and not is_video:
            is_live = True

    if asset.live_photo_group_id and asset.metadata is not None:
        asset.metadata.setdefault("live_photo_group_id", asset.live_photo_group_id)

    # Pano check
    is_pano = False
    metadata = asset.metadata or {}
    if metadata.get("is_pano"):
        is_pano = True
    else:
        width = _coerce_positive_number(asset.width) or _coerce_positive_number(metadata.get("w"))
        if width is None:
            width = _coerce_positive_number(metadata.get("width"))
        height = _coerce_positive_number(asset.height) or _coerce_positive_number(metadata.get("h"))
        if height is None:
            height = _coerce_positive_number(metadata.get("height"))
        aspect_ratio = _coerce_positive_number(metadata.get("aspect_ratio"))
        size_bytes = _coerce_positive_number(asset.size_bytes)
        if size_bytes is None:
            size_bytes = _coerce_positive_number(metadata.get("bytes"))

        if is_image_type and width > 0 and height > 0:
            aspect_ratio = width / height
            if aspect_ratio >= 2.0:
                if size_bytes is not None and size_bytes > 1 * 1024 * 1024:
                    is_pano = True
                elif size_bytes is None and width * height >= 1_000_000:
                    is_pano = True
        elif is_image_type and aspect_ratio is not None and aspect_ratio >= 2.0:
            if size_bytes is None or size_bytes > 1 * 1024 * 1024:
                is_pano = True

    micro_thumbnail = metadata.get("micro_thumbnail")
    micro_thumbnail_image = None
    if isinstance(micro_thumbnail, (bytes, bytearray, memoryview)):
        micro_thumbnail_image = image_loader.qimage_from_bytes(bytes(micro_thumbnail))

    width_value = (
        _coerce_positive_number(asset.width)
        or _coerce_positive_number(metadata.get("w"))
        or _coerce_positive_number(metadata.get("width"))
        or 0
    )
    height_value = (
        _coerce_positive_number(asset.height)
        or _coerce_positive_number(metadata.get("h"))
        or _coerce_positive_number(metadata.get("height"))
        or 0
    )

    return AssetDTO(
        id=asset.id,
        abs_path=abs_path,
        rel_path=asset.path,
        media_type=mt,
        created_at=asset.created_at,
        width=int(width_value),
        height=int(height_value),
        duration=asset.duration or 0.0,
        size_bytes=asset.size_bytes,
        metadata=metadata,
        is_favorite=asset.is_favorite,
        is_live=is_live,
        is_pano=is_pano,
        micro_thumbnail=micro_thumbnail_image,
    )


# ── GeotaggedAsset → DTO ─────────────────────────────────────────────────────

def geotagged_asset_to_dto(asset: object, library_root: Path) -> Optional[AssetDTO]:
    """Convert a *GeotaggedAsset* to an *AssetDTO* for display."""
    from iPhoto.library.manager import GeotaggedAsset

    if not isinstance(asset, GeotaggedAsset):
        return None

    abs_path = asset.absolute_path
    rel_path = Path(asset.library_relative)

    is_video = asset.is_video
    media_type = "video" if is_video else "image"

    metadata: dict = {
        "gps": {
            "latitude": asset.latitude,
            "longitude": asset.longitude,
        },
    }
    if asset.location_name:
        metadata["location"] = asset.location_name

    captured_at: Optional[datetime] = None
    asset_created_at = getattr(asset, "created_at", None)
    asset_captured_at = getattr(asset, "captured_at", None)

    if isinstance(asset_created_at, datetime):
        captured_at = asset_created_at
    elif isinstance(asset_captured_at, datetime):
        captured_at = asset_captured_at
    else:
        try:
            captured_at = datetime.fromtimestamp(abs_path.stat().st_mtime)
        except (FileNotFoundError, OSError, ValueError):
            captured_at = None

    return AssetDTO(
        id=asset.asset_id,
        abs_path=abs_path,
        rel_path=rel_path,
        media_type=media_type,
        created_at=captured_at,
        width=0,
        height=0,
        duration=asset.duration or 0.0,
        size_bytes=0,
        metadata=metadata,
        is_favorite=False,
        is_live=False,
        is_pano=False,
        micro_thumbnail=None,
    )


# ── scan-row → DTO ───────────────────────────────────────────────────────────

def scan_row_to_dto(
    view_root: Path,
    view_rel: str,
    row: dict,
) -> Optional[AssetDTO]:
    abs_path = view_root / view_rel
    rel_path = Path(view_rel)

    media_type_value = row.get("media_type")
    is_video = False
    if isinstance(media_type_value, str):
        is_video = media_type_value.lower() in {"1", "video"}
    elif isinstance(media_type_value, int):
        is_video = media_type_value == 1

    if not is_video and row.get("is_video"):
        is_video = True

    is_live = bool(
        row.get("is_live")
        or row.get("live_photo_group_id")
        or row.get("live_partner_rel")
    )
    if is_video:
        is_live = False

    media_type = MediaType.VIDEO.value if is_video else MediaType.IMAGE.value
    if is_live:
        media_type = MediaType.LIVE_PHOTO.value

    created_at = None
    dt_raw = row.get("dt")
    if isinstance(dt_raw, str):
        try:
            created_at = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
        except ValueError:
            created_at = None

    width = row.get("w") or row.get("width") or 0
    height = row.get("h") or row.get("height") or 0
    duration = row.get("dur") or row.get("duration") or 0.0
    size_bytes = row.get("bytes") or 0
    is_favorite = bool(row.get("featured") or row.get("favorite") or row.get("is_favorite"))
    is_pano = bool(row.get("is_pano"))

    return AssetDTO(
        id=str(row.get("id") or abs_path),
        abs_path=abs_path,
        rel_path=rel_path,
        media_type=media_type,
        created_at=created_at,
        width=int(width or 0),
        height=int(height or 0),
        duration=float(duration or 0.0),
        size_bytes=int(size_bytes or 0),
        metadata=dict(row),
        is_favorite=is_favorite,
        is_live=is_live,
        is_pano=is_pano,
        micro_thumbnail=row.get("micro_thumbnail"),
    )


def scan_row_matches_query(
    dto: AssetDTO,
    row: dict,
    query: AssetQuery,
) -> bool:
    if query.media_types:
        allowed = {media_type.value for media_type in query.media_types}
        if dto.media_type not in allowed:
            return False

    if query.is_favorite:
        is_favorite = bool(row.get("featured") or row.get("favorite") or row.get("is_favorite"))
        if not is_favorite:
            return False

    return True
