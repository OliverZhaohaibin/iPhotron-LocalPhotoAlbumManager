"""Live Photo pairing logic."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

from dateutil import parser

from ..config import LIVE_DURATION_PREFERRED, PAIR_TIME_DELTA_SEC
from ..domain.models.core import LiveGroup, normalise_content_id


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parser.isoparse(value)
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

    # If the asset has an explicit Content Identifier, it is definitely part of
    # a Live Photo pair regardless of the container format (e.g. MP4).
    # We must explicitly exclude the still image component (which shares the
    # same identifier) to avoid ambiguity if the caller checks predicates in
    # isolation or in a different order.
    if row.get("content_id") and not _is_photo(row):
        return True

    # Restrict Live Photo pairing to QuickTime movie sources. Generic videos like
    # MP4 clips should remain visible in the main asset list instead of being
    # paired and hidden behind a still image.
    mime = row.get("mime")
    if isinstance(mime, str) and mime.lower() == "video/quicktime":
        return True

    rel = row.get("rel")
    if isinstance(rel, str):
        return Path(rel).suffix.lower() in {".mov", ".qt"}

    return False


class _TimeCandidates:
    """Find the nearest unused video without rescanning exhausted timestamps."""

    def __init__(self, candidates: list[tuple[datetime, int, dict]]) -> None:
        buckets: dict[datetime, deque[tuple[int, dict]]] = defaultdict(deque)
        for moment, order, row in candidates:
            buckets[moment].append((order, row))
        self._times = sorted(buckets)
        self._rows = [buckets[moment] for moment in self._times]
        # Disjoint sets skip exhausted buckets in either direction. Each video
        # is discarded at most once per index, including dense same-time bursts.
        self._next = list(range(len(self._times) + 1))
        self._previous = list(range(len(self._times) + 1))

    @staticmethod
    def _find(parents: list[int], index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def _available(
        self, position: int, used: set[str], *, forward: bool
    ) -> int:
        while 0 <= position < len(self._times):
            rows = self._rows[position]
            while rows and rows[0][1]["rel"] in used:
                rows.popleft()
            if rows:
                return position
            self._next[position] = self._find(self._next, position + 1)
            self._previous[position + 1] = self._find(self._previous, position)
            position = (
                self._find(self._next, position)
                if forward
                else self._find(self._previous, position + 1) - 1
            )
        return position

    def nearest(self, moment: datetime, used: set[str]) -> dict | None:
        split = bisect_left(self._times, moment)
        right = self._available(self._find(self._next, split), used, forward=True)
        left = self._available(
            self._find(self._previous, split) - 1, used, forward=False
        )
        candidates = []
        for position in (left, right):
            if 0 <= position < len(self._times):
                order, row = self._rows[position][0]
                delta = abs((moment - self._times[position]).total_seconds())
                if delta <= PAIR_TIME_DELTA_SEC:
                    candidates.append((delta, order, row))
        return min(candidates, key=lambda item: item[:2])[2] if candidates else None


def _pairing_time(row: dict) -> datetime | None:
    moment = _parse_dt(row.get("dt"))
    if moment is not None and moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def pair_live(index_rows: List[Dict[str, object]]) -> List[LiveGroup]:
    """Pair still and motion assets into :class:`LiveGroup` objects."""

    photos: Dict[str, Dict[str, object]] = {}
    videos: Dict[str, Dict[str, object]] = {}
    for row in index_rows:
        if _is_photo(row):
            photos[row["rel"]] = row
        elif _is_video(row):
            videos[row["rel"]] = row

    matched: Dict[str, LiveGroup] = {}
    used_videos: set[str] = set()

    # 1) strong match by content_id
    video_by_cid: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for video in videos.values():
        cid = normalise_content_id(video.get("content_id"))
        if cid:
            video_by_cid[cid].append(video)
    for photo in photos.values():
        cid = normalise_content_id(photo.get("content_id"))
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

    if len(matched) == len(photos):
        return list(matched.values())

    by_stem: dict[str, list[tuple[datetime, int, dict]]] = defaultdict(list)
    by_folder: dict[str, list[tuple[datetime, int, dict]]] = defaultdict(list)
    for order, video in enumerate(videos.values()):
        if video["rel"] in used_videos:
            continue
        moment = _pairing_time(video)
        if moment is None:
            continue
        path = Path(video["rel"])
        candidate = (moment, order, video)
        by_stem[path.stem].append(candidate)
        by_folder[str(path.parent)].append(candidate)
    stem_indexes = {stem: _TimeCandidates(rows) for stem, rows in by_stem.items()}
    folder_indexes = {folder: _TimeCandidates(rows) for folder, rows in by_folder.items()}
    photo_times = {
        rel: _pairing_time(photo) for rel, photo in photos.items() if rel not in matched
    }

    # 2) medium match by same stem + time delta
    for photo in photos.values():
        if photo["rel"] in matched:
            continue
        moment = photo_times[photo["rel"]]
        candidates = stem_indexes.get(Path(photo["rel"]).stem)
        if moment is None or candidates is None:
            continue
        chosen = candidates.nearest(moment, used_videos)
        if chosen:
            used_videos.add(chosen["rel"])
            matched[photo["rel"]] = _build_group(photo, chosen, confidence=0.7)

    # 3) weak match by directory proximity
    for photo in photos.values():
        if photo["rel"] in matched:
            continue
        moment = photo_times[photo["rel"]]
        candidates = folder_indexes.get(str(Path(photo["rel"]).parent))
        if moment is None or candidates is None:
            continue
        chosen = candidates.nearest(moment, used_videos)
        if chosen:
            used_videos.add(chosen["rel"])
            matched[photo["rel"]] = _build_group(photo, chosen, confidence=0.5)

    return list(matched.values())


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
            elif current_score < best_score:
                continue
        # Prefer video with still_image_time over one without
        best_time = best.get("still_image_time")
        if still_time is not None and best_time is None:
            best = candidate
        elif still_time is not None and best_time is not None:
            # Prefer valid non-negative still_image_time,
            # then prefer smaller values.
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
