"""Default configuration values for iPhoto."""

from __future__ import annotations

from pathlib import Path
from typing import Final

# ``RECENTLY_DELETED_DIR_NAME`` stores the filesystem folder name that acts as
# the shared trash for the Basic Library.  The directory lives directly under
# the library root so assets removed from any album end up in a single
# collection, mirroring the behaviour users expect from other photo managers.
RECENTLY_DELETED_DIR_NAME: Final[str] = ".Trash"

DEFAULT_INCLUDE: Final[list[str]] = [
    (
        "**/*.{HEIC,heic,HEIF,heif,HEIFS,heifs,HEICF,heicf,"
        "JPG,jpg,JPEG,jpeg,PNG,png,MOV,mov,MP4,mp4,M4V,m4v,QT,qt,"
        "CR2,cr2,CR3,cr3,NEF,nef,NRW,nrw,ARW,arw,SRF,srf,SR2,sr2,"
        "ORF,orf,RW2,rw2,RAF,raf,PEF,pef,DNG,dng,RAW,raw,3FR,3fr,"
        "IIQ,iiq,RWL,rwl,SRW,srw,X3F,x3f,KDC,kdc,DCR,dcr,ERF,erf}"
    )
]
DEFAULT_EXCLUDE: Final[list[str]] = [
    "**/.iPhoto/**",
    "**/.DS_Store",
    "**/._*",
    f"**/{RECENTLY_DELETED_DIR_NAME}/**",
]
PAIR_TIME_DELTA_SEC: Final[float] = 3.0
LIVE_DURATION_PREFERRED: Final[tuple[float, float]] = (1.0, 3.5)
LOCK_EXPIRE_SEC: Final[int] = 30
THUMB_SIZES: Final[list[tuple[int, int]]] = [(256, 256), (512, 512)]

THUMBNAIL_SEEK_GUARD_SEC: Final[float] = 0.35

SCHEMA_DIR: Final[Path] = Path(__file__).resolve().parent / "schemas"
ALBUM_MANIFEST_NAMES: Final[list[str]] = [".iphoto.album.json", ".iPhoto/manifest.json"]
WORK_DIR_NAME: Final[str] = ".iPhoto"
EXPORT_DIR_NAME: Final[str] = "exported"

# ---------------------------------------------------------------------------
# UI interaction constants
# ---------------------------------------------------------------------------

LONG_PRESS_THRESHOLD_MS: Final[int] = 130
PREVIEW_WINDOW_DEFAULT_WIDTH: Final[int] = 640
PREVIEW_WINDOW_MUTED: Final[bool] = True
PREVIEW_WINDOW_CLOSE_DELAY_MS: Final[int] = 150
PREVIEW_WINDOW_CORNER_RADIUS: Final[int] = 18

# Maximum number of bytes to preload into memory for the active video. When the
# file on disk is smaller than this threshold the media controller will stream
# it from RAM to make seeking as responsive as possible.
VIDEO_MEMORY_CACHE_MAX_BYTES: Final[int] = 512 * 1024 * 1024

# When a video finishes playing we step backwards by this many milliseconds and
# pause so that the last frame remains visible instead of flashing to black.
VIDEO_COMPLETE_HOLD_BACKSTEP_MS: Final[int] = 80

# Minimum interval between successive play_asset() calls.  Rapid key presses
# (e.g. holding an arrow key) are coalesced so that only the last requested
# row triggers the expensive load/play cycle.
PLAY_ASSET_DEBOUNCE_MS: Final[int] = 60

# Delay/animation timings for the floating playback controls.
PLAYER_CONTROLS_HIDE_DELAY_MS: Final[int] = 2000
PLAYER_FADE_IN_MS: Final[int] = 150
PLAYER_FADE_OUT_MS: Final[int] = 300

# Keyboard shortcuts bump the output volume by this fixed percentage so repeated presses
# feel predictable regardless of the current level.  Keeping the value here allows both the
# window and controller layers to reference a shared constant instead of hard-coding their
# own step size.
VOLUME_SHORTCUT_STEP: Final[int] = 5
