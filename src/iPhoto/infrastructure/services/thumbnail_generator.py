from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageOps
import logging
import io

from src.iPhoto.application.interfaces import IThumbnailGenerator
from src.iPhoto.utils.image_loader import generate_micro_thumbnail
from src.iPhoto.utils.ffmpeg import extract_video_frame

LOGGER = logging.getLogger(__name__)

class PillowThumbnailGenerator(IThumbnailGenerator):
    """
    Generates thumbnails using Pillow for images and FFmpeg for videos.
    """

    def generate_micro_thumbnail(self, path: Path) -> Optional[str]:
        # Reuse existing utility
        return generate_micro_thumbnail(path)

    def generate(self, path: Path, size: Tuple[int, int]) -> Optional[Image.Image]:
        """
        Generate a thumbnail for the given path at the specified size (width, height).
        Returns a PIL Image object or None on failure.
        """
        try:
            # Determine if video based on extension
            video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
            if path.suffix.lower() in video_exts:
                return self._generate_video_thumbnail(path, size)

            # Default to Image
            return self._generate_image_thumbnail(path, size)

        except Exception as e:
            LOGGER.warning(f"Failed to generate thumbnail for {path}: {e}")
            return None

    def _generate_image_thumbnail(self, path: Path, size: Tuple[int, int]) -> Optional[Image.Image]:
        try:
            with Image.open(path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")

                # Apply EXIF orientation
                img = ImageOps.exif_transpose(img)

                # Create thumbnail using LANCZOS for quality
                img.thumbnail(size, Image.Resampling.LANCZOS)
                return img.copy()
        except Exception as e:
            LOGGER.warning(f"Pillow failed to open {path}: {e}")
            return None

    def _generate_video_thumbnail(self, path: Path, size: Tuple[int, int]) -> Optional[Image.Image]:
        try:
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
