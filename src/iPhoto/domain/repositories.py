from abc import ABC, abstractmethod
from typing import List, Optional
from pathlib import Path
from .models import Album, Asset
from .models.query import AssetQuery

class IAlbumRepository(ABC):
    @abstractmethod
    def get(self, id: str) -> Optional[Album]:
        pass

    @abstractmethod
    def get_by_path(self, path: Path) -> Optional[Album]:
        pass

    @abstractmethod
    def save(self, album: Album) -> None:
        pass

class IAssetRepository(ABC):
    @abstractmethod
    def get(self, id: str) -> Optional[Asset]:
        """Find single asset by ID"""
        pass

    @abstractmethod
    def get_by_path(self, path: Path) -> Optional[Asset]:
        """Find single asset by Path (PK)"""
        pass

    # Keeping old method for backward compatibility if needed,
    # but strictly we should move to find_by_query
    @abstractmethod
    def get_by_album(self, album_id: str) -> List[Asset]:
        """Find assets by album ID (Legacy)"""
        pass

    @abstractmethod
    def find_by_query(self, query: AssetQuery) -> List[Asset]:
        """Find assets by query object"""
        pass

    @abstractmethod
    def save(self, asset: Asset) -> None:
        """Save asset (insert or update)"""
        pass

    @abstractmethod
    def save_batch(self, assets: List[Asset]) -> None:
        """Batch save assets"""
        pass

    # Renamed save_all to save_batch in plan, keeping save_all as alias or removing?
    # Plan says save_batch. Let's add save_batch and maybe keep save_all as alias in impl.
    @abstractmethod
    def save_all(self, assets: List[Asset]) -> None:
        """Batch save assets (Legacy alias)"""
        pass

    @abstractmethod
    def delete(self, id: str) -> None:
        """Delete asset by ID"""
        pass

    @abstractmethod
    def count(self, query: AssetQuery) -> int:
        """Count assets matching query"""
        pass
