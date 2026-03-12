"""Legacy wrapper — the actual implementation now lives in ``demo/video/``.

This file is kept for backward compatibility.  Run via::

    python demo/video-demo.py          # still works
    python -m demo.video               # preferred

All public functions and classes are re-exported from the package modules.
"""

import sys
import os

# Ensure the demo/video/ package modules can be found via bare imports
_video_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video")
if _video_dir not in sys.path:
    sys.path.insert(0, _video_dir)

# Import QApplication for the legacy __main__ entry point below
from PySide6.QtWidgets import QApplication

# Re-export all public symbols for backward compatibility
from config import (  # noqa: F401
    BAR_HEIGHT, THUMB_WIDTH, CORNER_RADIUS, BORDER_THICKNESS,
    THUMB_LOGICAL_HEIGHT, ARROW_THICKNESS, THEME_COLOR, HOVER_COLOR,
    PYAV_MAX_WORKERS, MAX_FFMPEG_SLICES, FRAME_READ_BUFFER, STYLESHEET,
)
from probe import (  # noqa: F401
    _get_video_info, _get_video_info_pyav,
    _parse_rotation_from_ffprobe, _displaymatrix_has_vflip,
    _detect_rotation_pyav, HAS_PYAV,
)
from hwaccel import (  # noqa: F401
    _detect_hwaccel, _build_hwaccel_output_format,
    _hwaccel_cache,
)
from extraction import (  # noqa: F401
    _build_contact_sheet_cmd, _run_contact_sheet, _split_strip_bgra,
    _get_keyframe_timestamps_pyav, _snap_to_keyframes,
    _pyav_extract_segment, _extract_thumbnails_pyav,
    _build_popen_priority_kwargs, _lower_process_priority,
    _extract_frame_pipe,
    _try_extract_pipe_hwaccel, _try_extract_pipe_sw, _try_extract_pipe_auto,
    _run_pipe_cmd, _extract_single_frame, _build_single_pass_cmd,
)
from worker import ThumbnailWorker  # noqa: F401
from ui import (  # noqa: F401
    HandleButton, ThumbnailBar, VideoEditor,
)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoEditor()
    window.show()
    sys.exit(app.exec())