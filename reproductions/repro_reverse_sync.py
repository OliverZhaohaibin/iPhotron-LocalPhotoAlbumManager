
import sys
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

# Ensure src is in path so we can import iPhoto
sys.path.insert(0, "src")

from iPhoto.gui.facade import AppFacade
from iPhoto.library.manager import LibraryManager
from iPhoto.cache.index_store import IndexStore
from iPhoto.models.album import Album

def create_dummy_image(path: Path):
    from PIL import Image
    img = Image.new('RGB', (100, 100), color = 'red')
    img.save(path)

def check_db_favorite(root: Path, rel: str) -> bool:
    db_path = root / ".iPhoto" / "index.db"
    if not db_path.exists():
        print(f"DB not found at {db_path}")
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT is_favorite FROM assets WHERE rel = ?", (rel,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return bool(row[0])
    return False

def check_manifest_favorite(root: Path, rel: str) -> bool:
    album = Album.open(root)
    featured = album.manifest.get("featured", [])
    return rel in featured

def repro_reverse_sync():
    # Setup test environment
    test_dir = Path("reproductions/test_env_rev")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True)

    library_root = test_dir / "Library"
    library_root.mkdir()

    album_root = library_root / "SubAlbum"
    album_root.mkdir()

    photo_path = album_root / "photo.jpg"
    create_dummy_image(photo_path)

    # Initialize LibraryManager
    library_manager = LibraryManager()
    library_manager.bind_path(library_root)

    # Initialize Facade
    facade = AppFacade()
    facade.bind_library(library_manager)

    # Open Library Root (Aggregated View)
    print(f"Opening Library Root: {library_root}")
    facade.open_album(library_root)

    # Ensure scanning happens
    print("Rescanning library...")
    facade.rescan_current()

    # Also ensure SubAlbum is initialized so it has an index/manifest
    print("Scanning SubAlbum...")
    from iPhoto.app import rescan
    rescan(album_root)

    # Verify initial state
    print("Verifying initial state...")
    if check_db_favorite(library_root, "SubAlbum/photo.jpg"):
        print("ERROR: Already favorite in Library")
        sys.exit(2)

    if check_db_favorite(album_root, "photo.jpg"):
        print("ERROR: Already favorite in SubAlbum")
        sys.exit(2)

    # Toggle favorite on the Library Root
    # In Library Root, the rel path is "SubAlbum/photo.jpg"
    rel_in_library = "SubAlbum/photo.jpg"
    print(f"Toggling favorite for {rel_in_library} in Library Root")
    facade.toggle_featured(rel_in_library)

    # Check Library Root
    is_fav_lib = check_db_favorite(library_root, "SubAlbum/photo.jpg")
    print(f"Library Root 'is_favorite': {is_fav_lib}")

    # Check SubAlbum (Reverse Sync)
    is_fav_sub = check_db_favorite(album_root, "photo.jpg")
    print(f"SubAlbum 'is_favorite': {is_fav_sub}")

    manifest_fav_sub = check_manifest_favorite(album_root, "photo.jpg")
    print(f"SubAlbum Manifest 'featured': {manifest_fav_sub}")

    if is_fav_lib and not is_fav_sub:
        print("FAIL: Reverse synchronization failed. SubAlbum not updated.")
        sys.exit(1)
    elif is_fav_lib and is_fav_sub and manifest_fav_sub:
        print("SUCCESS: Both indexes and manifest updated.")
        sys.exit(0)
    else:
        print(f"ERROR: Unexpected state. Lib:{is_fav_lib}, Sub:{is_fav_sub}")
        sys.exit(2)

if __name__ == "__main__":
    repro_reverse_sync()
