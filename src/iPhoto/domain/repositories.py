from abc import ABC, abstractmethod
from typing import List, Optional
from pathlib import Path
from .models import Album, Asset

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
        pass

    @abstractmethod
    def get_by_album(self, album_id: str) -> List[Asset]:
        pass

    @abstractmethod
    def save(self, asset: Asset) -> None:
        pass

    @abstractmethod
    def save_all(self, assets: List[Asset]) -> None:
        pass

    @abstractmethod
    def delete(self, id: str) -> None:
        pass
