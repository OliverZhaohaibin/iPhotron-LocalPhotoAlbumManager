"""Hardware-acceleration detection (cached per process)."""

from __future__ import annotations

import os
import subprocess
import sys

_hwaccel_cache = None


def _detect_hwaccel():
    """
    Detect the best available ffmpeg hardware acceleration and GPU scale filter.

    Returns a dict with keys:
      - 'hwaccel': str or None  (e.g. 'd3d11va', 'cuda', 'videotoolbox', None)
      - 'scale_filter': str     (e.g. 'scale_d3d11', 'scale_cuda', 'scale')
      - 'download_filter': str  (e.g. 'hwdownload' or '')
      - 'pix_fmt': str          (output pixel format, always 'bgra')
    """
    global _hwaccel_cache
    if _hwaccel_cache is not None:
        return _hwaccel_cache

    _hwaccel_cache = {
        'hwaccel': None,
        'scale_filter': 'scale',
        'download_filter': '',
        'pix_fmt': 'bgra',
    }

    try:
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-hwaccels'],
            capture_output=True, text=True, startupinfo=startupinfo,
        )
        # Check both stdout AND stderr — ffmpeg versions vary in output routing
        hwaccels_text = (result.stdout + '\n' + result.stderr).lower()

        # Also check available filters for GPU scaling
        filter_result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-filters'],
            capture_output=True, text=True, startupinfo=startupinfo,
        )
        filters_text = (filter_result.stdout + '\n' + filter_result.stderr).lower()

        skip_words = ('hardware', 'acceleration', 'methods:')
        avail = [x for x in hwaccels_text.split() if x not in skip_words]
        print(f"[hwaccel] Available accelerators: {avail}")

        # Platform-dependent preference order:
        #   Windows: cuda (NVIDIA) > d3d11va (all GPUs) > qsv (Intel)
        #   macOS:   videotoolbox
        #   Linux:   cuda > vaapi
        if os.name == 'nt':
            candidates = [
                ('cuda', 'scale_cuda'),
                ('d3d11va', 'scale_d3d11'),
                ('qsv', 'scale_qsv'),
            ]
        elif sys.platform == 'darwin':
            candidates = [
                ('videotoolbox', 'scale_vt'),
            ]
        else:
            candidates = [
                ('cuda', 'scale_cuda'),
                ('vaapi', 'scale_vaapi'),
            ]

        for hwaccel_name, gpu_scale in candidates:
            if hwaccel_name in hwaccels_text:
                _hwaccel_cache['hwaccel'] = hwaccel_name
                if gpu_scale in filters_text:
                    _hwaccel_cache['scale_filter'] = gpu_scale
                else:
                    _hwaccel_cache['scale_filter'] = 'scale'
                _hwaccel_cache['download_filter'] = 'hwdownload'
                print(f"[hwaccel] Selected: {hwaccel_name}, "
                      f"GPU scale: {gpu_scale if gpu_scale in filters_text else 'N/A (CPU scale)'}")
                break

        if _hwaccel_cache['hwaccel'] is None:
            print("[hwaccel] No hardware acceleration detected, will use software decode")

    except Exception as e:
        print(f"[hwaccel] Detection failed: {e}")

    return _hwaccel_cache


def _build_hwaccel_output_format(hwaccel):
    """Return the -hwaccel_output_format value for a given hwaccel."""
    mapping = {
        'cuda': 'cuda',
        'd3d11va': 'd3d11',
        'videotoolbox': 'videotoolbox_vld',
        'vaapi': 'vaapi',
        'qsv': 'qsv',
    }
    return mapping.get(hwaccel, hwaccel)
