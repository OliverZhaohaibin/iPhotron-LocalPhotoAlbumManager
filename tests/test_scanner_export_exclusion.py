from pathlib import Path
from iPhoto.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, EXPORT_DIR_NAME
from iPhoto.io.scanner import gather_media_paths

def test_gather_media_paths_excludes_exported_directory(tmp_path: Path) -> None:
    # Setup: Create a normal file and a file in the exported directory
    normal_file = tmp_path / "normal.jpg"
    normal_file.touch()

    exported_dir = tmp_path / EXPORT_DIR_NAME
    exported_dir.mkdir()
    exported_file = exported_dir / "ignored.jpg"
    exported_file.touch()

    # Also test nested exported directory to ensure the exclusion works at any depth
    nested_dir = tmp_path / "subfolder"
    nested_dir.mkdir()
    nested_exported_dir = nested_dir / EXPORT_DIR_NAME
    nested_exported_dir.mkdir()
    nested_exported_file = nested_exported_dir / "nested_ignored.jpg"
    nested_exported_file.touch()

    # Execute
    image_paths, video_paths = gather_media_paths(tmp_path, DEFAULT_INCLUDE, DEFAULT_EXCLUDE)

    # Verify
    image_filenames = [p.name for p in image_paths]

    assert "normal.jpg" in image_filenames
    assert "ignored.jpg" not in image_filenames
    assert "nested_ignored.jpg" not in image_filenames
