"""Centralized RAW image processing via rawpy.

This module provides helpers for loading RAW camera files (CR2, NEF, ARW, etc.)
and converting them to standard RGB arrays or PIL Images.  All heavy I/O is
performed lazily so callers can decide whether to request a full-resolution
decode or a fast half-size preview suitable for thumbnails.

The public surface is intentionally small:

* :func:`is_raw_extension` – check if a suffix belongs to a RAW format.
* :func:`load_raw_to_pil` – decode a RAW file to a :class:`PIL.Image.Image`.
* :data:`RAW_EXTENSIONS` – the set of recognized RAW suffixes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

_LOGGER = logging.getLogger(__name__)

# ── RAW format extensions ────────────────────────────────────────────────────
# Covers all major camera vendors.  Extensions are stored lower-cased with a
# leading dot so they can be compared directly against ``Path.suffix.lower()``.
RAW_EXTENSIONS: frozenset[str] = frozenset({
    ".cr2", ".cr3",          # Canon
    ".nef", ".nrw",          # Nikon
    ".arw", ".srf", ".sr2",  # Sony
    ".orf",                  # Olympus
    ".rw2",                  # Panasonic
    ".raf",                  # Fujifilm
    ".pef",                  # Pentax
    ".dng",                  # Adobe DNG / Leica / others
    ".raw",                  # Generic
    ".3fr",                  # Hasselblad
    ".iiq",                  # Phase One
    ".rwl",                  # Leica
    ".srw",                  # Samsung
    ".x3f",                  # Sigma
    ".kdc", ".dcr",          # Kodak
    ".erf",                  # Epson
})


def is_raw_extension(suffix: str) -> bool:
    """Return *True* when *suffix* (e.g. ``".CR2"``) is a known RAW format."""
    return suffix.lower() in RAW_EXTENSIONS


# ── Lazy rawpy import ────────────────────────────────────────────────────────
def _import_rawpy():  # type: ignore[return]
    """Import rawpy on demand to avoid hard crashes when it is not installed."""
    try:
        import rawpy  # type: ignore[import-untyped]
        return rawpy
    except ImportError:
        return None


def load_raw_to_pil(
    path: Path,
    *,
    half_size: bool = False,
    target_size: Optional[Tuple[int, int]] = None,
) -> Optional["PIL.Image.Image"]:  # type: ignore[name-defined]  # noqa: F821
    """Decode a RAW file and return a PIL :class:`~PIL.Image.Image`.

    Parameters
    ----------
    path:
        Filesystem path to the RAW file.
    half_size:
        When *True* the RAW decoder uses half-resolution demosaicing which is
        roughly 4× faster than the default.  Ideal for thumbnail generation.
    target_size:
        Optional ``(width, height)`` bounding box.  If provided and *half_size*
        is *False* the function will still request half-size decoding when the
        full resolution exceeds double the target in both dimensions.

    Returns
    -------
    PIL.Image.Image | None
        An RGB image on success, or *None* when rawpy is unavailable or the
        file cannot be decoded.
    """
    rawpy = _import_rawpy()
    if rawpy is None:
        _LOGGER.debug("rawpy is not installed; cannot decode %s", path)
        return None

    try:
        from PIL import Image
    except ImportError:
        _LOGGER.debug("Pillow is not installed; cannot convert RAW to PIL")
        return None

    try:
        with rawpy.imread(str(path)) as raw:
            use_half = half_size
            if not use_half and target_size is not None:
                # Auto-select half-size when the target is much smaller than the
                # full sensor resolution – avoids wasting time on a full demosaic
                # that will be immediately downscaled.
                raw_h, raw_w = raw.raw_image.shape[:2]
                tw, th = target_size
                if raw_w > tw * 2 and raw_h > th * 2:
                    use_half = True

            rgb = raw.postprocess(
                use_camera_wb=True,
                half_size=use_half,
                no_auto_bright=False,
                output_bps=8,
            )
            return Image.fromarray(rgb)
    except Exception:
        _LOGGER.debug("Failed to decode RAW file %s", path, exc_info=True)
        return None


__all__ = [
    "RAW_EXTENSIONS",
    "is_raw_extension",
    "load_raw_to_pil",
]
