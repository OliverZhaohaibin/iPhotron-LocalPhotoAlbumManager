from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

class IMetadataProvider(ABC):
    """Interface for extracting metadata from media files."""

    @abstractmethod
    def get_metadata_batch(self, paths: List[Path]) -> List[Dict[str, Any]]:
        """
        Extract metadata for a batch of files.
        Returns a list of dictionaries, one for each path (order not guaranteed to match input).
        """
        pass

    @abstractmethod
    def normalize_metadata(self, root: Path, file_path: Path, raw_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize raw metadata into the application's standard index row format.
        """
        pass

class IThumbnailGenerator(ABC):
    """Interface for generating thumbnails."""

    @abstractmethod
    def generate_micro_thumbnail(self, path: Path) -> Optional[str]:
        """Generate a base64 encoded micro-thumbnail."""
        pass
