from pathlib import Path
from typing import Optional
from src.iPhoto.application.interfaces import IThumbnailGenerator
from src.iPhoto.utils.image_loader import generate_micro_thumbnail

class PillowThumbnailGenerator(IThumbnailGenerator):
    def generate_micro_thumbnail(self, path: Path) -> Optional[str]:
        # Reuse existing utility
        return generate_micro_thumbnail(path)
