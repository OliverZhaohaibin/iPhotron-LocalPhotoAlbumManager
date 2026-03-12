"""Shared constants and style configuration for the video thumbnail demo."""

from __future__ import annotations

import os

# --- 1. Icon path configuration (demo defaults; override per installation) ---
BASE_PATH = os.environ.get(
    "IPHOTO_ICON_DIR",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "src", "iPhoto", "gui", "ui", "icon",
    ),
)
ICON_PLAY = os.path.join(BASE_PATH, "play.fill.svg")
ICON_LEFT = os.path.join(BASE_PATH, "chevron.left.svg")
ICON_RIGHT = os.path.join(BASE_PATH, "chevron.right.svg")

# --- 2. Dimensions & style ---
BAR_HEIGHT = 50
THUMB_WIDTH = 70  # fallback default width
CORNER_RADIUS = 6
BORDER_THICKNESS = 4
THUMB_LOGICAL_HEIGHT = BAR_HEIGHT - 2 * BORDER_THICKNESS
ARROW_THICKNESS = 3
THEME_COLOR = "#3a3a3a"
HOVER_COLOR = "#505050"
TRIM_HIGHLIGHT_COLOR = "#FFD60A"
MIN_TRIM_GAP = 0.01          # minimum ratio gap between in/out handles
OUT_POINT_OFFSET_MS = 50     # ms before out-point to pause at

# --- 3. Parallelism tuning ---
PYAV_MAX_WORKERS = 4
MAX_FFMPEG_SLICES = 3
FRAME_READ_BUFFER = 3

# --- 4. Cache configuration ---
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "iphoto_video_thumbs")

# --- 5. QSS Stylesheet ---
STYLESHEET = f"""
QMainWindow {{
    background-color: #1e1e1e;
}}

/* Bottom area background */
QFrame#BottomControlFrame {{
    background-color: #252525;
    border-top: 1px solid #333;
}}

/* === Play button === */
QPushButton#PlayButton {{
    background-color: {THEME_COLOR};
    border: none;
    border-top-left-radius: {CORNER_RADIUS}px;
    border-bottom-left-radius: {CORNER_RADIUS}px;
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
    color: white;
}}
QPushButton#PlayButton:hover {{ background-color: {HOVER_COLOR}; }}

/* === Thumbnail strip middle container === */
QWidget#StripContainer {{
    background-color: transparent;
}}

QScrollArea {{
    background-color: transparent;
    border: none;
}}
"""
