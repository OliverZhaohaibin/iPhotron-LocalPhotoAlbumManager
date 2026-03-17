"""Live Photo pairing logic."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from time import perf_counter
from typing import Dict, Iterable, List, Tuple

from dateutil import parser

from .. import _native
from ..config import LIVE_DURATION_PREFERRED, PAIR_TIME_DELTA_SEC
from ..models.types import LiveGroup
from ..utils.logging import get_logger

LOGGER = get_logger()


def _parse_dt_us(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None

    native_ts = _native.parse_iso8601_to_unix_us(value)
    if native_ts is not None:
        return native_ts

    try:
        return int(parser.isoparse(value).timestamp() * 1_000_000)
    except (ValueError, TypeError):
        return None


_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".heifs",
    ".heicf",
}


def _is_photo(row: Dict[str, object]) -> bool:
    mime = row.get("mime")
    if isinstance(mime, str) and mime.lower().startswith("image/"):
        return True
    rel = row.get("rel")
    if isinstance(rel, str):
        return Path(rel).suffix.lower() in _IMAGE_EXTENSIONS
    return False


def _is_video(row: Dict[str, object]) -> bool:
    """Return True if the row represents a Live Photo motion component."""

    if row.get("content_id") and not _is_photo(row):
        return True

    mime = row.get("mime")
    if isinstance(mime, str) and mime.lower() == "video/quicktime":
        return True

    rel = row.get("rel")
    if isinstance(rel, str):
        return Path(rel).suffix.lower() in {".mov", ".qt"}

    return False


def _normalise_content_id(value: object) -> str | None:
    """Return a stable comparison key for Live Photo content identifiers."""

    if not isinstance(value, str):
        return None

    native_value = _native.normalise_content_id(value)
    if native_value is not None:
        return native_value

    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed.casefold()


def pair_live(index_rows: List[Dict[str, object]]) -> List[LiveGroup]:
    """Pair still and motion assets into :class:`LiveGroup` objects."""

    started_at = perf_counter()
    chunks = 0
    groups = _pair_live_native(index_rows)
    if groups is not None:
        chunks = max(1, (len(index_rows) + _native.PAIR_FEED_CHUNK_ITEMS - 1) // _native.PAIR_FEED_CHUNK_ITEMS)
        LOGGER.info(
            "pair_live finished in %.2fs (chunks=%d, items=%d)",
            perf_counter() - started_at,
            chunks,
            len(index_rows),
        )
        return groups

    groups = _pair_live_python(index_rows)
    if index_rows:
        chunks = max(1, (len(index_rows) + _native.PAIR_FEED_CHUNK_ITEMS - 1) // _native.PAIR_FEED_CHUNK_ITEMS)
    LOGGER.info(
        "pair_live finished in %.2fs (chunks=%d, items=%d)",
        perf_counter() - started_at,
        chunks,
        len(index_rows),
    )
    return groups


def _pair_live_native(index_rows: List[Dict[str, object]]) -> List[LiveGroup] | None:
    native_rows = [
        _native.NativePairRowInput(
            rel=row.get("rel") if isinstance(row.get("rel"), str) else None,
            mime=row.get("mime") if isinstance(row.get("mime"), str) else None,
            dt=row.get("dt") if isinstance(row.get("dt"), str) else None,
            content_id=row.get("content_id") if isinstance(row.get("content_id"), str) else None,
            dur=float(row["dur"]) if isinstance(row.get("dur"), (int, float)) else None,
            still_image_time=(
                float(row["still_image_time"])
                if isinstance(row.get("still_image_time"), (int, float))
                else None
            ),
        )
        for row in index_rows
    ]

    execution = _native.pair_rows(native_rows)
    if execution is None:
        return None

    groups: list[LiveGroup] = []
    for match in execution.matches:
        if match.still_index >= len(index_rows) or match.motion_index >= len(index_rows):
            return None
        groups.append(
            _build_group(
                index_rows[match.still_index],
                index_rows[match.motion_index],
                confidence=match.confidence,
            )
        )
    return groups


def _pair_live_python(index_rows: List[Dict[str, object]]) -> List[LiveGroup]:
    photos: Dict[str, Dict[str, object]] = {}
    videos: Dict[str, Dict[str, object]] = {}
    for row in index_rows:
        if _is_photo(row):
            photos[row["rel"]] = row
        elif _is_video(row):
            videos[row["rel"]] = row

    matched: Dict[str, LiveGroup] = {}
    used_videos: set[str] = set()

    video_by_cid: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for video in videos.values():
        cid = _normalise_content_id(video.get("content_id"))
        if cid:
            video_by_cid[cid].append(video)
    for photo in photos.values():
        cid = _normalise_content_id(photo.get("content_id"))
        if not cid or cid not in video_by_cid:
            continue
        candidates = [v for v in video_by_cid[cid] if v["rel"] not in used_videos]
        chosen = _select_best_video(candidates)
        if chosen:
            content_id = chosen.get("content_id") or photo.get("content_id")
            matched[photo["rel"]] = LiveGroup(
                id=f"live_{hash((photo['rel'], chosen['rel'])) & 0xFFFFFF:x}",
                still=photo["rel"],
                motion=chosen["rel"],
                content_id=content_id if isinstance(content_id, str) else None,
                still_image_time=chosen.get("still_image_time"),
                confidence=1.0,
            )
            used_videos.add(chosen["rel"])

    for photo in photos.values():
        if photo["rel"] in matched:
            continue
        stem = Path(photo["rel"]).stem
        candidates = [v for v in videos.values() if Path(v["rel"]).stem == stem]
        chosen = _match_by_time(photo, candidates, used_videos)
        if chosen:
            used_videos.add(chosen["rel"])
            matched[photo["rel"]] = _build_group(photo, chosen, confidence=0.7)

    for photo in photos.values():
        if photo["rel"] in matched:
            continue
        folder = str(Path(photo["rel"]).parent)
        candidates = [v for v in videos.values() if str(Path(v["rel"]).parent) == folder]
        chosen = _match_by_time(photo, candidates, used_videos)
        if chosen:
            used_videos.add(chosen["rel"])
            matched[photo["rel"]] = _build_group(photo, chosen, confidence=0.5)

    return list(matched.values())


def _match_by_time(
    photo: Dict[str, object],
    candidates: Iterable[Dict[str, object]],
    used_videos: set[str],
) -> Dict[str, object] | None:
    photo_dt = _parse_dt_us(photo.get("dt"))
    best: Tuple[float, Dict[str, object]] | None = None
    for candidate in candidates:
        if candidate["rel"] in used_videos:
            continue
        video_dt = _parse_dt_us(candidate.get("dt"))
        if photo_dt is None or video_dt is None:
            continue
        delta = abs(photo_dt - video_dt) / 1_000_000
        if delta > PAIR_TIME_DELTA_SEC:
            continue
        if best is None or delta < best[0]:
            best = (delta, candidate)
    return best[1] if best else None


def _select_best_video(candidates: Iterable[Dict[str, object]]) -> Dict[str, object] | None:
    best: Dict[str, object] | None = None
    preferred_min, preferred_max = LIVE_DURATION_PREFERRED
    for candidate in candidates:
        dur = candidate.get("dur")
        still_time = candidate.get("still_image_time")
        if best is None:
            best = candidate
            continue
        best_dur = best.get("dur")
        if dur is not None and best_dur is not None:
            current_score = _duration_score(dur, preferred_min, preferred_max)
            best_score = _duration_score(best_dur, preferred_min, preferred_max)
            if current_score > best_score:
                best = candidate
                continue
            if current_score < best_score:
                continue
        best_time = best.get("still_image_time")
        if still_time is not None and best_time is None:
            best = candidate
        elif still_time is not None and best_time is not None:
            if still_time >= 0 and (best_time < 0 or still_time < best_time):
                best = candidate
    return best


def _duration_score(duration: float, preferred_min: float, preferred_max: float) -> float:
    if duration < preferred_min:
        return -preferred_min + duration
    if duration > preferred_max:
        return -duration
    midpoint = (preferred_min + preferred_max) / 2
    return preferred_max - abs(midpoint - duration)


def _build_group(photo: Dict[str, object], video: Dict[str, object], confidence: float) -> LiveGroup:
    return LiveGroup(
        id=f"live_{hash((photo['rel'], video['rel'])) & 0xFFFFFF:x}",
        still=photo["rel"],
        motion=video["rel"],
        content_id=video.get("content_id") or photo.get("content_id"),
        still_image_time=video.get("still_image_time"),
        confidence=confidence,
    )
