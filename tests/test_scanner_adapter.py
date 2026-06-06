from __future__ import annotations

from pathlib import Path

from PIL import Image

from iPhoto.io import scanner_adapter


def test_process_media_paths_falls_back_to_minimal_row_when_metadata_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    asset = root / "broken.jpg"
    asset.write_bytes(b"jpeg-data")

    monkeypatch.setattr(
        scanner_adapter._metadata_provider,
        "get_metadata_batch",
        lambda paths: [],
    )
    monkeypatch.setattr(
        scanner_adapter._metadata_provider,
        "normalize_metadata",
        lambda _root, _path, _raw: (_ for _ in ()).throw(RuntimeError("exif failure")),
    )
    monkeypatch.setattr(
        scanner_adapter._thumbnail_generator,
        "generate_micro_thumbnail",
        lambda _path: None,
    )

    rows = list(scanner_adapter.process_media_paths(root, [asset], []))

    assert len(rows) == 1
    row = rows[0]
    assert row["rel"] == "broken.jpg"
    assert row["bytes"] == len(b"jpeg-data")
    assert row["media_type"] == 0
    assert row["face_status"] == "pending"
    assert row["id"].startswith("as_")
    assert row["thumbnail_state"] == "failed"
    assert row["thumb_error"] == "thumbnail_unavailable"


def test_process_media_paths_keeps_row_when_thumbnail_generation_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    asset = root / "thumb_fail.jpg"
    asset.write_bytes(b"jpeg-data")

    monkeypatch.setattr(
        scanner_adapter._metadata_provider,
        "get_metadata_batch",
        lambda paths: [],
    )
    monkeypatch.setattr(
        scanner_adapter._metadata_provider,
        "normalize_metadata",
        lambda _root, _path, _raw: {
            "rel": "thumb_fail.jpg",
            "bytes": len(b"jpeg-data"),
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1704067200000000,
            "id": "as_thumb_fail",
            "mime": "image/jpeg",
            "media_type": 0,
            "face_status": "pending",
        },
    )
    monkeypatch.setattr(
        scanner_adapter._thumbnail_generator,
        "generate_micro_thumbnail",
        lambda _path: (_ for _ in ()).throw(RuntimeError("thumb failure")),
    )

    rows = list(scanner_adapter.process_media_paths(root, [asset], []))

    assert len(rows) == 1
    row = rows[0]
    assert row["rel"] == "thumb_fail.jpg"
    assert row["id"] == "as_thumb_fail"
    assert row["face_status"] == "pending"
    assert row["thumbnail_state"] == "failed"
    assert row["thumb_error"] == "thumbnail_unavailable"
    assert "micro_thumbnail" not in row


def test_process_media_paths_sets_ready_thumbnail_before_visible_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    asset = root / "ready.jpg"
    asset.write_bytes(b"jpeg-data")

    monkeypatch.setattr(
        scanner_adapter._metadata_provider,
        "get_metadata_batch",
        lambda paths: [],
    )
    monkeypatch.setattr(
        scanner_adapter._metadata_provider,
        "normalize_metadata",
        lambda _root, _path, _raw: {
            "rel": "ready.jpg",
            "bytes": len(b"jpeg-data"),
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1704067200000000,
            "id": "as_ready",
            "mime": "image/jpeg",
            "media_type": 0,
            "face_status": "pending",
        },
    )
    monkeypatch.setattr(
        scanner_adapter._thumbnail_generator,
        "generate_micro_thumbnail",
        lambda _path: b"thumb-bytes",
    )
    monkeypatch.setattr(
        scanner_adapter._thumbnail_generator,
        "generate",
        lambda _path, _size: Image.new("RGB", (32, 32), "red"),
    )

    rows = list(scanner_adapter.process_media_paths(root, [asset], []))

    assert len(rows) == 1
    row = rows[0]
    assert row["thumbnail_state"] == "ready"
    assert row["micro_thumbnail"] == b"thumb-bytes"
    assert row["thumb_cache_key"]
    cache_file = root / ".iPhoto" / "cache" / "thumbs" / f"{row['thumb_cache_key']}.jpg"
    assert cache_file.exists()


def test_process_media_paths_overwrites_existing_full_thumbnail_for_rescanned_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    asset = root / "ready.jpg"
    asset.write_bytes(b"new-jpeg-data")
    cache_dir = root / ".iPhoto" / "cache" / "thumbs"
    cache_file = scanner_adapter.thumbnail_cache_file(cache_dir, asset)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (512, 512), "green").save(cache_file, format="JPEG")

    monkeypatch.setattr(
        scanner_adapter._metadata_provider,
        "get_metadata_batch",
        lambda paths: [],
    )
    monkeypatch.setattr(
        scanner_adapter._metadata_provider,
        "normalize_metadata",
        lambda _root, _path, _raw: {
            "rel": "ready.jpg",
            "bytes": len(b"new-jpeg-data"),
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1704067200000000,
            "id": "as_ready",
            "mime": "image/jpeg",
            "media_type": 0,
            "face_status": "pending",
        },
    )
    monkeypatch.setattr(
        scanner_adapter._thumbnail_generator,
        "generate_micro_thumbnail",
        lambda _path: b"thumb-bytes",
    )
    monkeypatch.setattr(
        scanner_adapter._thumbnail_generator,
        "generate",
        lambda _path, _size: Image.new("RGB", (32, 32), "red"),
    )

    rows = list(scanner_adapter.process_media_paths(root, [asset], []))

    assert rows[0]["thumb_cache_key"]
    red, green, blue = Image.open(cache_file).getpixel((0, 0))
    assert red > 200
    assert green < 80
    assert blue < 80


def test_scan_album_refreshes_cached_row_missing_full_thumbnail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    asset = root / "cached.jpg"
    asset.write_bytes(b"jpeg-data")
    stat = asset.stat()
    existing = {
        "cached.jpg": {
            "rel": "cached.jpg",
            "id": "as_cached",
            "bytes": stat.st_size,
            "ts": int(stat.st_mtime * 1_000_000),
            "thumbnail_state": "ready",
            "micro_thumbnail": b"old-micro",
        }
    }
    metadata_calls = []
    generate_calls = []

    monkeypatch.setattr(
        scanner_adapter._metadata_provider,
        "get_metadata_batch",
        lambda paths: metadata_calls.append(paths) or [],
    )
    monkeypatch.setattr(
        scanner_adapter._thumbnail_generator,
        "generate_micro_thumbnail",
        lambda _path: b"new-micro",
    )

    def generate(path, size):
        generate_calls.append((path, size))
        return Image.new("RGB", (32, 32), "blue")

    monkeypatch.setattr(scanner_adapter._thumbnail_generator, "generate", generate)

    rows = list(
        scanner_adapter.scan_album(
            root,
            ["*.jpg"],
            [],
            existing_index=existing,
        )
    )

    assert metadata_calls == []
    assert len(generate_calls) == 1
    assert rows[0]["rel"] == "cached.jpg"
    assert rows[0]["thumbnail_state"] == "ready"
    assert rows[0]["micro_thumbnail"] == b"new-micro"
    assert rows[0]["thumb_cache_key"]
    cache_file = root / ".iPhoto" / "cache" / "thumbs" / f"{rows[0]['thumb_cache_key']}.jpg"
    assert cache_file.exists()
