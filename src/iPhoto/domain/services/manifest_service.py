import json
from pathlib import Path
from iPhoto.errors import AlbumNotFoundError

class ManifestService:
    """JSON manifest file read/write — extracted from Legacy Album.open()"""

    def read_manifest(self, album_path: Path) -> dict:
        manifest_path = album_path / "manifest.json"
        if not manifest_path.exists():
            raise AlbumNotFoundError(f"Manifest not found: {manifest_path}")
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def write_manifest(self, album_path: Path, data: dict) -> None:
        manifest_path = album_path / "manifest.json"
        tmp_path = manifest_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_path.replace(manifest_path)
