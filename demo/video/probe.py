"""Video probing — ffprobe and PyAV based metadata extraction."""

from __future__ import annotations

import json
import os
import subprocess

try:
    import av as _av_module
    HAS_PYAV = True
except ImportError:
    _av_module = None
    HAS_PYAV = False


# ---------------------------------------------------------------------------
# ffprobe-based probing
# ---------------------------------------------------------------------------

def _get_video_info(video_path):
    """Probe video display dimensions, duration, rotation and vflip via ffprobe.

    Returns (display_w, display_h, duration, rotation, vflip) where
    display_w/display_h are the dimensions after applying rotation metadata,
    rotation is 0/90/180/270 degrees, and vflip indicates vertical flip.
    """
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-probesize', '32768', '-analyzeduration', '0',
            '-select_streams', 'v:0',
            '-show_entries',
            'stream=width,height,duration:stream_tags=rotate'
            ':stream_side_data=rotation,displaymatrix',
            '-of', 'json',
            video_path,
        ]
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(
            cmd, capture_output=True, text=True, startupinfo=startupinfo,
        )
        data = json.loads(result.stdout)
        stream = data['streams'][0]
        width = int(stream['width'])
        height = int(stream['height'])
        duration = float(stream.get('duration', 0))
        if duration == 0:
            cmd_fmt = [
                'ffprobe', '-v', 'error',
                '-probesize', '32768', '-analyzeduration', '0',
                '-show_entries', 'format=duration',
                '-of', 'json', video_path,
            ]
            res = subprocess.run(
                cmd_fmt, capture_output=True, text=True,
                startupinfo=startupinfo,
            )
            data_fmt = json.loads(res.stdout)
            duration = float(data_fmt['format']['duration'])

        # --- Detect rotation / flip ---
        rotation, vflip = _parse_rotation_from_ffprobe(stream)

        # Swap dimensions for 90°/270° rotation
        if rotation in (90, 270):
            width, height = height, width

        return width, height, duration, rotation, vflip
    except Exception as e:
        print(f"Error getting video info: {e}")
        return 0, 0, 0, 0, False


def _parse_rotation_from_ffprobe(stream_dict):
    """Extract rotation and vflip from ffprobe stream dict.

    Checks both ``tags.rotate`` (older containers) and
    ``side_data_list[].rotation`` (modern display-matrix).

    Returns (rotation_degrees, vflip) where rotation is normalised
    to 0/90/180/270.
    """
    rotation = 0
    vflip = False

    # Method 1: stream_tags.rotate (MP4/MOV with older muxers)
    tags = stream_dict.get('tags', {})
    if 'rotate' in tags:
        try:
            rotation = int(tags['rotate'])
        except (ValueError, TypeError):
            pass

    # Method 2: side_data_list[].rotation (display-matrix, ffprobe >= 4.x)
    if rotation == 0:
        for sd in stream_dict.get('side_data_list', []):
            if sd.get('side_data_type') == 'Display Matrix':
                try:
                    r = float(sd.get('rotation', 0))
                    # ffprobe reports CW rotation as negative
                    rotation = int(-r) % 360
                except (ValueError, TypeError):
                    pass
                # Detect vflip from display matrix string
                dm = sd.get('displaymatrix', '')
                if dm:
                    vflip = _displaymatrix_has_vflip(dm)
                break

    # Normalise to [0, 360)
    rotation = rotation % 360
    # Snap to nearest 90° (some encoders write e.g. 89 or 91)
    if rotation not in (0, 90, 180, 270):
        rotation = min((0, 90, 180, 270), key=lambda x: abs(x - rotation))

    return rotation, vflip


def _displaymatrix_has_vflip(dm_string):
    """Heuristic: detect vertical flip from ffprobe displaymatrix string.

    The display matrix is printed as 3 rows of hex values. A pure vflip
    has a negative [1][1] element (second row, second value).
    """
    try:
        lines = [l.strip() for l in dm_string.strip().split('\n') if l.strip()]
        if len(lines) >= 2:
            # Each line has 3 hex values like "00010000 00000000 00000000"
            parts = lines[1].split()
            if len(parts) >= 2:
                val = int(parts[1], 16)
                # Sign-extend 32-bit value
                if val >= 0x80000000:
                    val -= 0x100000000
                return val < 0
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# PyAV-based probing
# ---------------------------------------------------------------------------

def _get_video_info_pyav(video_path):
    """Probe video display dimensions, duration, rotation and vflip via PyAV.

    Returns (display_w, display_h, duration, rotation, vflip).
    """
    try:
        container = _av_module.open(video_path)
        stream = container.streams.video[0]
        width = stream.codec_context.width
        height = stream.codec_context.height
        duration = 0.0
        if stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        if duration <= 0 and container.duration:
            duration = container.duration / _av_module.time_base

        # Detect rotation from stream metadata or frame display-matrix
        rotation, vflip = _detect_rotation_pyav(stream, container)
        container.close()

        # Swap to display dimensions for 90°/270° rotation
        if rotation in (90, 270):
            width, height = height, width

        return width, height, duration, rotation, vflip
    except Exception as e:
        print(f"[pyav] Probe failed: {e}")
        return 0, 0, 0, 0, False


def _detect_rotation_pyav(stream, container=None):
    """Detect rotation and vflip from a PyAV video stream.

    Checks two sources in order:
    1. ``stream.metadata['rotate']`` — older MP4/MOV with rotation tag.
    2. First decoded frame — modern containers using display-matrix side
       data (most modern phones).  Requires *container* so we can decode
       one frame.  Checks ``frame.rotation`` first, then falls back to
       ``frame.side_data['DISPLAYMATRIX']`` dict access.

    Returns (rotation_degrees, vflip) normalised to 0/90/180/270.
    """
    rotation = 0
    vflip = False

    # Method 1: metadata tag (common in MP4/MOV from older phones)
    try:
        rotate_val = stream.metadata.get('rotate', '')
        if rotate_val:
            rotation = int(rotate_val)
    except (ValueError, TypeError, AttributeError):
        pass

    # Method 2: decode one frame and read rotation from frame-level data.
    # PyAV exposes the display-matrix rotation on *frames*, not streams.
    # frame.rotation returns CCW degrees in [-180, 180].
    if rotation == 0 and container is not None:
        try:
            container.seek(0, stream=stream)
            for frame in container.decode(stream):
                # Try frame.rotation (direct attribute, PyAV >= 12)
                fr = getattr(frame, 'rotation', None)
                if fr is not None and fr != 0:
                    rotation = int(-fr) % 360  # CCW → CW
                else:
                    # Fallback: frame.side_data dict
                    fsd = getattr(frame, 'side_data', None)
                    if fsd and hasattr(fsd, 'get'):
                        dm = fsd.get('DISPLAYMATRIX')
                        if dm is not None:
                            if isinstance(dm, dict):
                                r = float(dm.get('rotation', 0))
                                rotation = int(-r) % 360
                                # Detect vflip from matrix values
                                sy = dm.get('sy') or dm.get('d')
                                if sy is not None:
                                    try:
                                        if float(sy) < 0:
                                            vflip = True
                                    except (ValueError, TypeError):
                                        pass
                            elif isinstance(dm, (int, float)):
                                rotation = int(-dm) % 360
                break  # only need the first frame
        except Exception:
            pass

    # Normalise
    rotation = rotation % 360
    if rotation not in (0, 90, 180, 270):
        rotation = min((0, 90, 180, 270), key=lambda x: abs(x - rotation))

    return rotation, vflip
