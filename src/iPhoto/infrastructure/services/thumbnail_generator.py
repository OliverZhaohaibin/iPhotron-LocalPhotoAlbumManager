from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageOps
import logging
import io

from iPhoto.application.interfaces import IThumbnailGenerator
from iPhoto.utils.image_loader import generate_micro_thumbnail
from iPhoto.utils.ffmpeg import extract_video_frame
from iPhoto.core.raw_processor import is_raw_extension, load_raw_to_pil

LOGGER = logging.getLogger(__name__)

class PillowThumbnailGenerator(IThumbnailGenerator):
    """
    Generates thumbnails using Pillow for images and FFmpeg for videos.
    """

    def generate_micro_thumbnail(self, path: Path) -> Optional[str]:
        # Reuse existing utility
        if not path.exists():
            return None
        return generate_micro_thumbnail(path)

    def generate(self, path: Path, size: Tuple[int, int]) -> Optional[Image.Image]:
        """
        Generate a thumbnail for the given path at the specified size (width, height).
        Returns a PIL Image object or None on failure.
        """
        try:
            if not path.exists():
                return None
            # Determine if video based on extension
            video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
            if path.suffix.lower() in video_exts:
                return self._generate_video_thumbnail(path, size)

            # RAW camera files require rawpy for decoding.
            if is_raw_extension(path.suffix):
                return self._generate_raw_thumbnail(path, size)

            # Default to Image
            return self._generate_image_thumbnail(path, size)

        except Exception as e:
            LOGGER.warning(f"Failed to generate thumbnail for {path}: {e}")
            return None

    def _generate_image_thumbnail(self, path: Path, size: Tuple[int, int]) -> Optional[Image.Image]:
        try:
            with Image.open(path) as img:
                # Use faster BILINEAR resampling for thumbnails (3-5x speedup vs LANCZOS)
                resample = Image.Resampling.BILINEAR

                # Use draft mode for JPEGs to load at reduced resolution
                if img.format == "JPEG":
                    draft_size = (min(size[0] * 4, img.width), min(size[1] * 4, img.height))
                    img.draft("RGB", draft_size)

                if img.mode != "RGB":
                    img = img.convert("RGB")

                # Apply EXIF orientation only if needed (check before transpose)
                exif = img.getexif()
                if exif:
                    img = ImageOps.exif_transpose(img)

                img.thumbnail(size, resample)
                return img.copy()
        except Exception as e:
            LOGGER.warning(f"Pillow failed to open {path}: {e}")
            return None

    def _generate_raw_thumbnail(self, path: Path, size: Tuple[int, int]) -> Optional[Image.Image]:
        """Generate a thumbnail from a RAW camera file using rawpy."""
        try:
            pil_img = load_raw_to_pil(path, half_size=True, target_size=size)
            if pil_img is None:
                return None
            pil_img.thumbnail(size, Image.Resampling.LANCZOS)
            return pil_img
        except Exception as e:
            LOGGER.warning(f"rawpy failed to generate thumbnail for {path}: {e}")
            return None

    def _generate_video_thumbnail(self, path: Path, size: Tuple[int, int]) -> Optional[Image.Image]:
        try:
            if not path.exists():
                return None
            data = extract_video_frame(path, at=0.0, scale=size, format="jpeg")
            if data:
                with io.BytesIO(data) as bio:
                    img = Image.open(bio)
                    img.load()
                    return img.copy()
        except Exception as e:
            LOGGER.warning(f"FFmpeg failed to extract frame from {path}: {e}")
            return None
        return None
