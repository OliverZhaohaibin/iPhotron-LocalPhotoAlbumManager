from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageOps
import logging

from src.iPhoto.application.interfaces import IThumbnailGenerator
from src.iPhoto.utils.image_loader import generate_micro_thumbnail

LOGGER = logging.getLogger(__name__)

class PillowThumbnailGenerator(IThumbnailGenerator):
    """
    Generates thumbnails using Pillow.
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
            # Open the image file
            with Image.open(path) as img:
                # Convert to RGB to handle various formats (RGBA, P, etc.)
                if img.mode != "RGB":
                    img = img.convert("RGB")

                # Create thumbnail (preserves aspect ratio)
                # Image.thumbnail modifies in-place
                # We use ImageOps.fit to fill the box if needed, or stick to thumbnail for speed.
                # Grid view usually prefers consistent sizing, so `fit` (center crop) might be better
                # but `thumbnail` is safer for now to avoid cropping heads.
                # Actually, standard thumbnails are often fit-to-box.
                # Let's use `thumbnail` which fits WITHIN the box.

                # Note: PIL.Image.thumbnail is destructive to the object, but we opened it fresh.
                # We want a high-quality resize.
                img.thumbnail(size, Image.Resampling.LANCZOS)

                # Copy the image because the context manager will close the file
                return img.copy()
        except Exception as e:
            LOGGER.warning(f"Failed to generate thumbnail for {path}: {e}")
            return None
